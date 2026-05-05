"""Core agent loop — the ReAct reasoning cycle.

Handles the full cycle: prompt assembly → budget check → LLM call →
tool execution → episode save → cost logging → fact extraction →
intent detection → relationship moments → context header.
"""

from __future__ import annotations

import json
import logging
from typing import Any

# capability_registry is re-exported as a module attribute on this
# loop module because tests (test_telegram_chaos.py:57 et al.) patch
# ``windyfly.agent.loop.capability_registry`` directly. The attribute
# also gets shadowed by a parameter in ``_dispatch_tool_call`` and a
# local re-import in ``agent_respond`` — both intentional and safe;
# ruff's F811 noise is suppressed below.
from windyfly.agent.capabilities import capability_registry  # noqa: F401
from windyfly.agent.context_header import maybe_prepend_header
from windyfly.agent.emotion_detector import detect_emotional_context, get_emotional_trend
from windyfly.agent.intent_detector import detect_intent
from windyfly.agent.models import call_llm, estimate_cost
from windyfly.agent.offline import get_offline_response, is_online
from windyfly.agent.prompt import assemble_prompt
from windyfly.agent.tracing import set_request_id, request_id_short
from windyfly.control_panel import get_sliders
from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.cost_tracker import check_budget
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes, save_episode
from windyfly.memory.intents import create_intent
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import Priority, WriteQueue
from windyfly.observability.events import log_event
from windyfly.personality.engine import apply_adaptive_overrides
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Per-session token accumulator. Critical: the key MUST be the
# session_id, not a process-global counter. A global counter
# accumulates across ALL sessions for the lifetime of the process,
# so after ~50 conversations the context-header indicator shows
# 🔴 0% even though every individual session has plenty of context.
# The LLM, seeing 🔴 0% in the prior turn's header (now part of
# conversation history), starts emulating "I'm out of context" →
# bot returns terse "I'm not responding" replies.
#
# Surfaced 2026-04-28 by stress_v7_endurance: iters 1-30 fine, then
# from iter ~31 onward the bot returned 19-character replies because
# the global counter had crossed the 🔴 threshold.
_session_tokens: dict[str, int] = {}


def _bump_session_tokens(session_id: str, count: int) -> int:
    """Add `count` to the session's running total and return the new total."""
    new_total = _session_tokens.get(session_id, 0) + count
    _session_tokens[session_id] = new_total
    return new_total

# Default tool re-loop rounds (overridden by slider)
_DEFAULT_TOOL_ROUNDS = 3


def _user_message_mentions_local(text: str) -> bool:
    """Heuristic: does the message reference a local file/path/repo?

    Triggers the FS-tool nudge in agent_respond. Intentionally
    conservative — false positives just add ~50 tokens to the prompt;
    false negatives mean the LLM web_searches when it should
    fs.read_file.

    Note: includes "github" / "git" / "repo" because for Grant (and
    most users with local clones) "go to my github" means "look at the
    local clone in ~/" — surfaced when the bot tried web search for
    'kit-army-config' instead of fs.list_directory ~/kit-army-config.
    """
    if not text:
        return False
    lower = text.lower()
    # Path-shaped strings (./x, /Users/, ~/, src/, etc.)
    if any(seg in text for seg in ("/Users/", "~/", "./", "../")):
        return True
    if "/" in text and any(
        ext in lower for ext in (".md", ".py", ".ts", ".js", ".toml",
                                  ".json", ".yaml", ".yml", ".txt",
                                  ".sh", ".rs", ".go")
    ):
        return True
    # Possessive references to local artifacts + repo-name triggers.
    # Repo names mirror the user's ~/ layout — every name in this list
    # corresponds to a real top-level repo dir on Grant's machine.
    triggers = (
        "my repo", "my windy", "my project", "my folder", "my file",
        "my directory", "my notes", "my code",
        "github", "git repo", " repo ", " repo?", " repo.", " repo,",
        "in src/", "in tests/", "in docs/", "in scripts/",
        "windy-agent", "windy-pro", "windy-cloud", "windy-mail",
        "windy-chat", "windy-code", "windy-clone", "windy-infra",
        "windy-pro-cloud", "windy-pro-mobile", "windy-pro-updates",
        "windy-pro-cloud-data",
        "kit-army", "kit-army-config", "lockbox", "access_lockbox",
        "eternitas", "nachocrunch",
        "soul.md", "claude.md", "readme.md", "memory.md",
    )
    return any(t in lower for t in triggers)


# Confabulation detector (below). Tuned from the Apr 21 smoke-battery
# incident: GLM-4.7 answered "Done! Created `~/scratch/test-undo.md`"
# with no tool_calls at all — the file never existed. Same for delete,
# undo, and grep ("Found 48 matches across 13 files" — fully made up).
# Matching is conservative on both sides: the user must have asked for
# an action AND the response must claim success; otherwise we'd flag
# every "I wrote it down" acknowledgement.
_ACTION_REQUEST_TRIGGERS = (
    "write ", "create ", "save ", "make a file", "put a file",
    "add a file", "append ",
    "delete ", "remove ", "rm ", "unlink ",
    "undo", "revert", "restore", "roll back", "rollback",
    "grep", "search for", "search my", "search the",
    "find the word", "find occurrences", "look for", "find all",
    "move ", "rename ", "copy ",
)
_SUCCESS_CLAIM_MARKERS = (
    "done!", "done.", "✅", "✓",
    "created ", "wrote ", "saved ", "written to ",
    "deleted ", "removed ", "unlinked ",
    "restored ", "undone", "reverted ", "rolled back",
    "moved ", "renamed ", "copied ",
    "found ",  # "Found 48 matches" / "Found 0 files"
)


def _looks_confabulated(user_message: str, response_text: str) -> bool:
    """Did the LLM claim success on an action it didn't actually take?

    The caller only invokes this when ``tool_calls`` is empty/None —
    so if this returns True, the LLM produced a "Done!"-shaped reply
    without ever touching a capability.

    Conservative: requires BOTH the user's message to contain an
    action-request trigger AND the response to contain a success-claim
    marker. A normal Q&A like "what's up?" → "Not much!" won't trip.
    """
    if not user_message or not response_text:
        return False
    lower_req = user_message.lower()
    if not any(t in lower_req for t in _ACTION_REQUEST_TRIGGERS):
        return False
    lower_resp = response_text.lower()
    return any(m in lower_resp for m in _SUCCESS_CLAIM_MARKERS)


_CONFAB_RETRY_SYSTEM = (
    "STOP. Your previous reply claimed to have completed an action "
    "(e.g., 'Done!', 'Created', 'Deleted', 'Found N matches'), but "
    "you did not call any tool. You cannot complete file or shell "
    "actions by describing them in prose — you must invoke the "
    "matching capability (fs.write_file, fs.delete_file, "
    "fs.undo_last_action, fs.grep_files, fs.move_file, shell.exec, "
    "etc.). Retry the user's request by calling the right tool. If "
    "the needed capability is not in your tool list, say so "
    "explicitly — do not fake success."
)

_CONFAB_TRUTH_FALLBACK = (
    "I almost made that up — I was about to reply as if I'd done it, "
    "but I didn't actually call the tool that performs the action. "
    "Something's wrong with my tool-picker on this request. Try "
    "rephrasing, or run /caps to see which capabilities I can "
    "actually invoke right now."
)


def _dispatch_tool_call(
    fn_name: str,
    fn_args: Any,
    tool_registry: Any,
    capability_registry: Any,  # noqa: F811 - intentional shadow of module re-export
    band: Any,
    capability_denied_exc: type[BaseException],
) -> str:
    """Route an LLM tool call to the right registry.

    Capability registry wins over legacy tool registry when both have a
    name collision (Wave 2 #5 will migrate legacy tools to caps; until
    then prefer the new path). Capability calls go through invoke_sync
    so band-gating + audit fire automatically. The result is JSON-
    encoded if non-string so the LLM can parse it.
    """
    cap = capability_registry.get(fn_name)
    if cap is not None:
        if isinstance(fn_args, str):
            try:
                fn_args = json.loads(fn_args) if fn_args else {}
            except json.JSONDecodeError:
                return json.dumps({
                    "error": f"capability {fn_name}: invalid JSON args",
                })
        try:
            result = capability_registry.invoke_sync(fn_name, fn_args, band)
        except capability_denied_exc as e:
            logger.info("Capability denied: %s", e)
            return json.dumps({"error": f"capability_denied: {e}"})
        except Exception as e:
            logger.warning("Capability %s failed: %s", fn_name, e)
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return result if isinstance(result, str) else json.dumps(result, default=str)

    if tool_registry is None:
        logger.warning("LLM called unknown tool (no registry): %s", fn_name)
        return json.dumps({"error": f"Unknown tool: {fn_name}"})

    try:
        return tool_registry.execute(fn_name, fn_args)
    except KeyError:
        logger.warning("LLM called unknown tool: %s", fn_name)
        return json.dumps({"error": f"Unknown tool: {fn_name}"})


def agent_respond(
    config: dict[str, Any],
    db: Database,
    write_queue: WriteQueue,
    user_message: str,
    session_id: str,
    tool_registry: ToolRegistry | None = None,
    band: Any = None,
) -> str:
    """Process a user message and return the agent's response.

    Full pipeline:
      1. Assemble prompt (personality + memory + context + user message)
      1.5. Friction detection (Never Wrong Twice)
      1.6. Emotional awareness (stress/excitement detection + trend injection)
      1.75. Budget enforcement (check daily budget before LLM call)
      2. Call LLM (with tool schemas if registry provided)
      2.5. Tool-call re-loop (execute tools, feed results back, up to 3 rounds)
      3. Save episodes (user + assistant) via write queue
      4. Log cost via write queue
      5. Extract facts and upsert nodes via write queue
      6. Detect and store intents via write queue
      7. Return the agent's response text

    Args:
        config: Loaded config dict.
        db: Database instance.
        write_queue: WriteQueue for async DB writes.
        user_message: The user's message.
        session_id: Current session ID.
        tool_registry: Optional ToolRegistry for function calling.
        band: Capability passport band for this session. Defaults to
            Band.OWNER for back-compat with existing channel callers.
            Future channels with passport-based band will pass it
            explicitly so /pulse from grandma sees fewer capabilities
            than /pulse from Grant.

    Returns:
        The agent's response text.
    """
    # Resolve band default lazily so we don't import the Capability
    # Plane at module-load time (avoids circular-import surprises).
    from windyfly.agent.capabilities import (  # noqa: F811 - lazy reimport intentional
        Band,
        CapabilityDenied,
        capability_registry,
        set_current_session_id,
    )
    if band is None:
        band = Band.OWNER

    # Wave 14 tracing spine: stamp this request with a UUID at entry so
    # every downstream write (episodes, agent_actions, cost_ledger,
    # events) and every log line flowing through the request_id filter
    # can be correlated. Cheap, bounded, and zero-coupling — downstream
    # callers reach get_request_id() lazily on the contextvar. Return
    # value isn't kept; the side-effect of populating the contextvar is
    # the entire point.
    set_request_id()
    # Wave 14b session-id propagation: stamp the contextvar that the
    # capability audit hooks read so every ``agent_actions`` row this
    # request causes carries the originating session_id. Pre-fix, every
    # ledger row had ``session_id IS NULL`` (caught by stress harness
    # 2026-04-26 — couldn't correlate tool invocations to test cases).
    set_current_session_id(session_id)
    logger.info("[req:%s] agent_respond start session=%s band=%s",
                request_id_short(), session_id, band)

    # 0. Empty-message guard. Anthropic returns 400 on empty user
    # content (``messages.0: user messages must have non-empty
    # content``); other providers behave the same. We refuse locally
    # so a stray newline / whitespace from a channel adapter never
    # burns an LLM call and never surfaces as a generic
    # "Sorry, something went wrong" to the user. Caught by the windy-0
    # stress harness 2026-04-26 (stress_v1.py edge_empty case).
    if not (user_message or "").strip():
        logger.info(
            "[req:%s] empty user message — short-circuiting",
            request_id_short(),
        )
        return "Did you mean to send something? I didn't catch a message."

    # 0.5. Pause / kill-switch check. If the operator hit /pause OR
    # the auto-pause hit a burn-rate threshold, the flag file at
    # ~/.windy/.paused exists and we MUST NOT call the LLM. The bot
    # stays alive on Telegram (polling, watchdog, identity intact)
    # but routes every message to a static reply. Survives restart
    # by design — operator must explicitly /resume.
    #
    # The /resume command itself is processed in the channel
    # adapter BEFORE the loop runs, so it can clear the flag and
    # then continue normally.
    from windyfly.agent.spend_monitor import is_paused, pause_reason
    if is_paused():
        info = pause_reason()
        logger.info(
            "[req:%s] paused — short-circuiting (reason=%s)",
            request_id_short(), info.get("reason", "?"),
        )
        when = (info.get("ts") or "").replace("T", " ")[:16]
        why = info.get("reason", "manual pause")
        return (
            f"💤 I'm paused (since {when} — {why}). I'm awake on "
            f"Telegram but I won't make any LLM calls until you "
            f"say /resume."
        )

    # 1. Assemble prompt
    # Pass current session's pct_remaining so the prompt assembler
    # can inject the low-context hint at < 10%.
    _max_ctx = 200_000
    _used = _session_tokens.get(session_id, 0)
    _pct_remaining = max(0.0, 100.0 - (_used / _max_ctx) * 100)
    messages = assemble_prompt(
        config, db, user_message, session_id,
        pct_remaining=_pct_remaining,
        band=band,
    )

    # 1.0.5 — Capability-aware tool-selection nudge.
    # When the user references a local path / file / repo / folder, the
    # LLM (especially GLM-4) tends to default to web_search instead of
    # fs.read_file even though the FS capability is in its tool list.
    # Inject a tight instruction per-call so it picks correctly. Only
    # fires when the registry actually has fs.* capabilities AND the
    # message looks path-ish — otherwise we waste tokens.
    if (
        capability_registry.get("fs.read_file")
        and _user_message_mentions_local(user_message)
    ):
        messages.insert(1, {
            "role": "system",
            "content": (
                "The user is referring to something on the local "
                "machine (a file, repo, folder, or path). You have "
                "fs.read_file, fs.list_directory, fs.glob, and "
                "fs.grep_files available. Use those FIRST before "
                "falling back to web_search or asking the user to "
                "paste content.\n\n"
                "IMPORTANT: when the user says 'my github' or 'my X "
                "github repo' or 'my <name> repo', they almost always "
                "mean a LOCAL CLONE in their home directory, not the "
                "online GitHub. Try fs.list_directory ~/<name> first. "
                "Do not refuse with 'I can't access GitHub' — try the "
                "local path.\n\n"
                "Local repos that exist for this user (in ~/):\n"
                "  windy-agent, windy-pro, windy-cloud, windy-mail,\n"
                "  windy-chat, windy-code, windy-clone, windy-infra,\n"
                "  windy-pro-cloud, windy-pro-mobile, windy-pro-updates,\n"
                "  kit-army-config, eternitas, nachocrunch,\n"
                "  windy-0-soul (this instance's identity + config),\n"
                "  any other <name>-soul repo for other instances.\n"
                "Common file names: SOUL.md, CLAUDE.md, README.md, "
                "windyfly.toml, config.toml, ACCESS_LOCKBOX.md."
            ),
        })

    # 1.1. First interaction magic
    from windyfly.agent.first_interaction import (
        is_first_interaction,
        mark_first_interaction_done,
        get_first_interaction_prompt,
        should_nudge_capabilities,
        mark_capabilities_nudged,
        get_capability_nudge,
    )

    if is_first_interaction(db):
        first_prompt = get_first_interaction_prompt(user_message, config)
        if first_prompt:
            messages.insert(1, {"role": "system", "content": first_prompt})
        mark_first_interaction_done(db)
    elif should_nudge_capabilities(db):
        messages.insert(1, {"role": "system", "content": get_capability_nudge()})
        mark_capabilities_nudged(db)

    # 1.5. Friction detection (Never Wrong Twice)
    from windyfly.agent.failure_detector import detect_friction, handle_friction

    recent = get_recent_episodes(db, limit=1, session_id=session_id)
    prev_agent_msg = None
    for ep in recent:
        if ep["role"] == "assistant":
            prev_agent_msg = ep["content"]
            break

    friction = detect_friction(user_message, prev_agent_msg)
    if friction:
        extra_instruction = handle_friction(db, write_queue, friction)
        if extra_instruction:
            messages.insert(1, {"role": "system", "content": extra_instruction})

    # 1.6. Emotional awareness
    emotional_context = detect_emotional_context(user_message)

    # Read emotional_sensitivity slider (0=ignore, 10=hyper-attuned)
    personality_config = config.get("personality", {})
    loop_sliders = get_sliders(db, config_defaults=personality_config)
    emo_sensitivity = loop_sliders.get("emotional_sensitivity", 5)

    if emo_sensitivity > 0:
        emo_window = max(1, emo_sensitivity)
        emotional_trend = get_emotional_trend(db, session_id, window=emo_window)
    else:
        emotional_trend = "neutral"  # Slider at 0 → ignore emotions

    # 1.65. Adaptive mode — override sliders based on emotion (gated by toggle)
    if loop_sliders.get("adaptive_mode", 5) >= 5:
        loop_sliders = apply_adaptive_overrides(loop_sliders, emotional_context, emotional_trend)

    if emotional_trend == "sustained_stress":
        messages.insert(1, {
            "role": "system",
            "content": (
                "The user seems stressed. Be extra concise and supportive. "
                "Don't suggest new things right now. Focus on what they're asking."
            ),
        })
    elif emotional_trend == "excited":
        messages.insert(1, {
            "role": "system",
            "content": "The user is excited! Match their enthusiasm and energy.",
        })

    # 1.75. Budget enforcement
    model = config.get("agent", {}).get("default_model", "gpt-4o-mini")

    # Creativity slider → LLM temperature (0→0.0, 10→1.0)
    creativity = loop_sliders.get("creativity", 5)
    temperature = round(creativity / 10.0, 2)

    # Response length slider → max_tokens (0→250, 10→4000)
    response_length = loop_sliders.get("response_length", 5)
    max_tokens = 250 + (response_length * 375)

    # 1.8. Offline detection — fall back to local model if API unreachable
    if not is_online():
        logger.warning("LLM API unreachable — entering offline mode")
        from windyfly.agent.offline import queue_message
        context = [{"role": m["role"], "content": m["content"]} for m in messages[-5:]]
        offline_response = get_offline_response(user_message, context)
        # Queue message for processing when back online
        queue_message(user_message, session_id)
        # Still save episodes so history is preserved
        write_queue.enqueue(Priority.HIGH, save_episode, db, "user", user_message, session_id=session_id)
        write_queue.enqueue(Priority.HIGH, save_episode, db, "assistant", offline_response, session_id=session_id)
        log_event(db, write_queue, "offline.fallback", {"message": user_message[:100]})
        return offline_response

    estimated_input_tokens = len(user_message.split()) * 3  # rough estimate
    proposed_cost = estimate_cost(model, estimated_input_tokens, max_tokens // 2)
    budget = check_budget(db, config, proposed_cost)

    if not budget["allowed"]:
        alert = budget.get("alert", "")
        return alert or (
            f"I've hit my daily budget "
            f"(${budget['daily_spend']:.2f} of ${budget['daily_budget']:.2f}). "
            f"I'll be back tomorrow, or you can increase the budget in settings."
        )

    # Warning-tier alerts: log for the operator, do NOT inject into the
    # user-facing reply. Hard-cap blocking is handled above (line ~425)
    # and still returns a user-visible refusal. Reproduced 2026-04-26
    # via stress harness v4: at 80%+ every Telegram reply was prepended
    # with "Heads up: I've used $X.XX of your $5.00 daily budget" — the
    # bot's own ops state leaking into normal conversation.
    if budget.get("alert"):
        logger.warning("[budget] %s", budget["alert"])

    # 2. Call LLM (with tools if registry provided)
    legacy_tools = tool_registry.get_schemas() if tool_registry else []
    capability_tools = capability_registry.tool_schemas_for_band(band)
    tools = (legacy_tools + capability_tools) if (legacy_tools or capability_tools) else None

    # call_llm raises RuntimeError when every provider in the chain
    # fails (e.g., 401 burst from Anthropic during a rate-limit
    # window, or all configured providers in cooldown). The offline
    # path above only catches the *proactive* probe failure — it
    # cannot detect a chain that goes from healthy → throttled
    # mid-turn. v14 stress 2026-05-02 surfaced this: 37 prompts
    # cleared, then the 38th hit a 401 cascade and the bot returned
    # a stack trace instead of a friendly message. Route the
    # exception into the SAME offline-fallback so the user always
    # gets a coherent reply and the bot never crashes mid-
    # conversation.
    try:
        result = call_llm(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            config=config,
        )
    except RuntimeError as e:
        msg = str(e)
        if "providers in chain" in msg or "providers" in msg.lower():
            logger.warning(
                "[req:%s] LLM provider chain exhausted — falling back "
                "to offline mode (last_error=%s)",
                request_id_short(), msg[:200],
            )
            from windyfly.agent.offline import queue_message
            context = [{"role": m["role"], "content": m["content"]} for m in messages[-5:]]
            offline_response = get_offline_response(user_message, context)
            queue_message(user_message, session_id)
            write_queue.enqueue(Priority.HIGH, save_episode, db, "user", user_message, session_id=session_id)
            write_queue.enqueue(Priority.HIGH, save_episode, db, "assistant", offline_response, session_id=session_id)
            log_event(db, write_queue, "offline.chain_exhausted", {
                "message": user_message[:100],
                "error": msg[:200],
            })
            return offline_response
        # Non-chain RuntimeError (something we didn't anticipate) —
        # let it bubble so we see it in logs and don't silently
        # swallow real bugs.
        raise

    response_text = result["content"]
    input_tokens = result["input_tokens"]
    output_tokens = result["output_tokens"]
    tool_calls = result.get("tool_calls")

    # Observability: log what the LLM decided to do. When debugging "why
    # did the bot pick web_search instead of fs.read_file?" this single
    # line is the answer in 5 seconds flat.
    if tool_calls:
        picked = [
            f"{tc['function']['name']}({str(tc['function'].get('arguments', ''))[:60]})"
            for tc in tool_calls
        ]
        logger.info("LLM picked: %s (response_text=%d chars)",
                    ", ".join(picked), len(response_text or ""))
    else:
        logger.info("LLM responded text-only (%d chars, no tool calls)",
                    len(response_text or ""))

    # 2.5. Tool-call re-loop (ReAct cycle)
    tool_executed = False  # gate for the confabulation guard below
    if tool_calls and (tool_registry or capability_registry.count() > 0):
        tool_executed = True
        max_tool_rounds = loop_sliders.get("tool_reloop_rounds", _DEFAULT_TOOL_ROUNDS)
        for _round in range(max_tool_rounds):
            # Execute each tool call
            tool_results = []
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]
                logger.info("Executing tool: %s (round %d)", fn_name, _round + 1)
                tool_result = _dispatch_tool_call(
                    fn_name, fn_args, tool_registry, capability_registry,
                    band, CapabilityDenied,
                )
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

            # Append assistant tool_call message + tool results
            messages.append({
                "role": "assistant",
                "content": response_text or "",
                "tool_calls": tool_calls,
            })
            messages.extend(tool_results)

            # Call LLM again with tool results
            result = call_llm(
                messages, model=model, temperature=temperature,
                max_tokens=max_tokens, tools=tools, config=config,
            )
            response_text = result["content"]
            input_tokens += result["input_tokens"]
            output_tokens += result["output_tokens"]
            tool_calls = result.get("tool_calls")

            if not tool_calls:
                break

    # 2.6. Confabulation guard — trust-preserving net.
    # If the LLM ended text-only and the user asked for an action, the
    # response may be a plausible fake ("Done! Created the file" with no
    # tool call). Detect that, retry once with a forcing system prompt,
    # and if the retry still lies, replace the response with a truthful
    # fallback so we never ship a lie downstream. Surfaced by Grant's
    # 2026-04-21 live smoke battery — write/delete/undo/grep all
    # returned fake "Done!" replies with zero tool invocations.
    if (
        not tool_executed
        and not tool_calls
        and _looks_confabulated(user_message, response_text)
    ):
        logger.warning(
            "Confabulation suspected — text-only success claim with no "
            "tool_calls. user_message=%r response_preview=%r",
            user_message[:120], (response_text or "")[:200],
        )
        log_event(db, write_queue, "agent.confabulation_detected", {
            "session_id": session_id,
            "user_preview": user_message[:120],
            "response_preview": (response_text or "")[:200],
            "stage": "initial",
        })
        messages.append({
            "role": "assistant",
            "content": response_text or "",
        })
        messages.append({
            "role": "system",
            "content": _CONFAB_RETRY_SYSTEM,
        })
        retry = call_llm(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens, tools=tools, config=config,
        )
        response_text = retry["content"]
        input_tokens += retry["input_tokens"]
        output_tokens += retry["output_tokens"]
        retry_tool_calls = retry.get("tool_calls")

        if retry_tool_calls:
            # Retry elected to use tools — run them through the same
            # dispatch path and fold the result into response_text.
            tool_calls = retry_tool_calls
            logger.info(
                "Confabulation retry recovered: LLM picked %s",
                ", ".join(tc["function"]["name"] for tc in tool_calls),
            )
            tool_results = []
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]
                logger.info("Executing tool: %s (confab-retry)", fn_name)
                tool_result = _dispatch_tool_call(
                    fn_name, fn_args, tool_registry, capability_registry,
                    band, CapabilityDenied,
                )
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })
            messages.append({
                "role": "assistant",
                "content": response_text or "",
                "tool_calls": tool_calls,
            })
            messages.extend(tool_results)
            followup = call_llm(
                messages, model=model, temperature=temperature,
                max_tokens=max_tokens, tools=tools, config=config,
            )
            response_text = followup["content"]
            input_tokens += followup["input_tokens"]
            output_tokens += followup["output_tokens"]
            tool_calls = followup.get("tool_calls")
        elif _looks_confabulated(user_message, response_text):
            # Retry still lied. Replace the response so we don't ship
            # the fake success to the user.
            logger.error(
                "Confabulation retry also failed — replacing response "
                "with truth fallback. last_response=%r",
                (response_text or "")[:200],
            )
            log_event(db, write_queue, "agent.confabulation_detected", {
                "session_id": session_id,
                "user_preview": user_message[:120],
                "response_preview": (response_text or "")[:200],
                "stage": "retry",
            })
            response_text = _CONFAB_TRUTH_FALLBACK

    # 2.9. Analytics tracking
    try:
        from windyfly.analytics import track
        track(db, "message_received")
        track(db, "message_sent")
        if tool_calls:
            for tc in (tool_calls if isinstance(tool_calls, list) else []):
                track(db, "tool_invoked", {"tool_name": tc.get("function", {}).get("name", "unknown")})
    except Exception:
        pass  # Analytics should never break the agent

    # 2.95. Empty-after-tool-loop defense. The tool loop can exit with
    # ``response_text = ""`` when the LLM only called tools and never
    # produced text content (e.g., shape_shift → shape_shift_restore
    # → no answer; or web_search → fs.glob → ran out of tool rounds).
    # We never want to ship empty silence to the user — they'll think
    # the bot crashed. Reproduced 2026-04-26 via stress harness v2
    # G_naming case (LLM picked shape_shift on a brainstorm prompt
    # and never circled back to actually brainstorming).
    if not (response_text or "").strip():
        recent_tool_names = [
            tc.get("function", {}).get("name", "?")
            for tc in (tool_calls if isinstance(tool_calls, list) else [])
        ]
        logger.warning(
            "[req:%s] tool loop exited with no text content "
            "(last_round_tool_calls=%s) — substituting fallback",
            request_id_short(), recent_tool_names,
        )
        try:
            log_event(db, write_queue, "agent.empty_after_tools", {
                "session_id": session_id,
                "user_preview": user_message[:120],
                "tool_names": recent_tool_names,
            })
        except Exception:
            pass
        response_text = (
            "Hmm — I started working on that and got distracted by my "
            "own tools without actually answering you. Try asking again, "
            "maybe more directly? (If you're seeing this a lot, my "
            "tool-picker is over-eager — let me know.)"
        )

    # 3. Save episodes via write queue (HIGH priority)
    cost_usd = estimate_cost(model, input_tokens, output_tokens)

    write_queue.enqueue(
        Priority.HIGH,
        save_episode,
        db, "user", user_message,
        session_id=session_id,
        emotional_context=emotional_context,
    )
    write_queue.enqueue(
        Priority.HIGH,
        save_episode,
        db, "assistant", response_text,
        session_id=session_id,
        token_count=output_tokens,
        cost_usd=cost_usd,
    )

    # 4. Log cost via write queue (MEDIUM priority)
    write_queue.enqueue(
        Priority.MEDIUM,
        log_cost,
        db, model, input_tokens, output_tokens, cost_usd,
    )

    # 5. Extract facts and upsert nodes (MEDIUM priority)
    _extract_and_store_facts(db, write_queue, user_message)

    # 6. Intent detection (MEDIUM priority) — regex fast-path + LLM fallback
    proactivity = loop_sliders.get("proactivity", 5)
    intent = detect_intent(user_message, config=config, proactivity=proactivity)
    if intent and intent.get("has_intent"):
        # Dedup: don't create if a similar active intent already exists
        from windyfly.memory.intents import find_similar_intent
        existing = find_similar_intent(db, intent["description"])
        if not existing:
            write_queue.enqueue(
                Priority.MEDIUM,
                create_intent,
                db,
                intent["description"],
                origin=intent["origin"],
            )

    # 7. Log event for observability
    log_event(db, write_queue, "agent.respond", {
        "session_id": session_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "had_tool_calls": bool(tool_calls),
        "emotional_context": emotional_context,
    })

    # 7.5. Relationship moments — extract emotional snapshots
    warmth = loop_sliders.get("warmth", 5)
    if emotional_context != "neutral" and warmth >= 3:
        _extract_relationship_moment(
            db, write_queue, config, user_message, response_text,
            emotional_context, session_id,
        )

    # 7.6. Agent journal — periodic reflective entries
    _maybe_write_journal_entry(
        db, write_queue, config, user_message, response_text,
        emotional_context, session_id,
    )

    # 8. Context gas tank header (signature feature)
    session_total = _bump_session_tokens(
        session_id, input_tokens + output_tokens,
    )
    response_text = maybe_prepend_header(response_text, session_total)

    return response_text


def _extract_and_store_facts(
    db: Database,
    write_queue: WriteQueue,
    user_message: str,
) -> None:
    """Extract obvious facts from the user message and store as nodes.

    Simple pattern-based extraction for Phase 0. More sophisticated
    LLM-based extraction will come in later phases.

    Patterns detected:
    - "My name is X"
    - "I am X" / "I'm X"
    - "I live in X"
    - "I like X" / "I love X"
    - "I work at X" / "I work as X"
    """
    import re

    patterns = [
        (r"(?i)my name is (.+?)(?:\.|,|!|\?|$)", "person", "user_name", "user_stated"),
        (r"(?i)i(?:'m| am) (.+?)(?:\.|,|!|\?|$)", "trait", "user_trait", "user_stated"),
        (r"(?i)i live in (.+?)(?:\.|,|!|\?|$)", "location", "user_location", "user_stated"),
        (r"(?i)i (?:like|love) (.+?)(?:\.|,|!|\?|$)", "preference", "user_preference", "user_stated"),
        (r"(?i)i work (?:at|as|for) (.+?)(?:\.|,|!|\?|$)", "work", "user_work", "user_stated"),
    ]

    for pattern, node_type, name_prefix, source in patterns:
        match = re.search(pattern, user_message)
        if match:
            value = match.group(1).strip()
            if len(value) > 2 and len(value) < 100:
                write_queue.enqueue(
                    Priority.MEDIUM,
                    upsert_node,
                    db,
                    node_type,
                    f"{name_prefix}:{value}",
                    metadata={"raw_statement": user_message[:200]},
                    source=source,
                    epistemic_status="user_stated",
                )


def _extract_relationship_moment(
    db: Database,
    write_queue: WriteQueue,
    config: dict[str, Any],
    user_message: str,
    response_text: str,
    emotional_context: str,
    session_id: str,
) -> None:
    """Extract a relationship moment from an emotional interaction.

    Creates a one-line emotional snapshot like:
      "User was frustrated → we debugged together → solved it → relief"

    Stored as type=relationship_moment for future prompt injection.
    """
    try:
        moment_prompt = (
            "Summarize this interaction as a one-line emotional snapshot of a shared "
            "experience between friends. Format: 'emotion → what happened → outcome'. "
            "Keep it under 25 words.\n\n"
            f"User ({emotional_context}): {user_message[:300]}\n"
            f"Agent: {response_text[:300]}"
        )

        result = call_llm(
            [
                {"role": "system", "content": "You write brief emotional summaries."},
                {"role": "user", "content": moment_prompt},
            ],
            model=config.get("agent", {}).get("default_model", "gpt-4o-mini"),
            temperature=0.3,
            max_tokens=50,
            config=config,
        )

        moment = result["content"].strip().strip('"')
        if moment and len(moment) > 10:
            write_queue.enqueue(
                Priority.LOW,
                upsert_node,
                db,
                "relationship_moment",
                f"moment:{moment[:200]}",
                metadata={
                    "session_id": session_id,
                    "emotional_context": emotional_context,
                    "summary": moment,
                },
                source="agent_observed",
                epistemic_status="verified",
            )
            logger.debug("Relationship moment saved: %s", moment[:80])

    except Exception as e:
        logger.debug("Relationship moment extraction failed: %s", e)


_session_interaction_counts: dict[str, int] = {}


def _maybe_write_journal_entry(
    db: Database,
    write_queue: WriteQueue,
    config: dict[str, Any],
    user_message: str,
    response_text: str,
    emotional_context: str,
    session_id: str,
) -> None:
    """Conditionally write a reflective journal entry.

    Triggers every 10th interaction in the SAME session, OR when
    emotion is detected. Same class of bug as #93: pre-fix this was
    a module-level global counter that accumulated across all
    sessions, so journal cadence was unpredictable in any multi-
    session scenario (stress harness, multi-user bot, long-running
    process). Now keyed per session_id.
    """
    count = _session_interaction_counts.get(session_id, 0) + 1
    _session_interaction_counts[session_id] = count

    # Only write every 10th interaction in this session, or on
    # emotional moments.
    if count % 10 != 0 and emotional_context == "neutral":
        return

    try:
        journal_prompt = (
            "You are an AI agent writing a brief journal entry about a recent "
            "interaction with your user. Write 1-2 sentences from your perspective "
            "about what you discussed, what you learned, and how the user seemed. "
            "Be reflective and genuine, like a diary entry.\n\n"
            f"User said: {user_message[:300]}\n"
            f"You responded about: {response_text[:200]}\n"
            f"User's mood: {emotional_context}"
        )

        result = call_llm(
            [
                {"role": "system", "content": "You write brief, genuine diary entries."},
                {"role": "user", "content": journal_prompt},
            ],
            model=config.get("agent", {}).get("default_model", "gpt-4o-mini"),
            temperature=0.6,
            max_tokens=80,
            config=config,
        )

        entry = result["content"].strip()
        if entry and len(entry) > 15:
            write_queue.enqueue(
                Priority.LOW,
                upsert_node,
                db,
                "journal_entry",
                f"journal:{entry[:200]}",
                metadata={
                    "session_id": session_id,
                    "emotional_context": emotional_context,
                    "entry": entry,
                },
                source="agent_journal",
                epistemic_status="verified",
            )
            logger.debug("Journal entry written: %s", entry[:80])

    except Exception as e:
        logger.debug("Journal entry failed: %s", e)
