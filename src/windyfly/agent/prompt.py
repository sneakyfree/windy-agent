"""Prompt assembly for the Windy Fly agent.

Assembles the full message list for an LLM call:
system prompt (personality + mode), memory context (recent episodes),
relevant knowledge nodes, and the user's current message.
"""

from __future__ import annotations

import json
from typing import Any

from windyfly.control_panel import get_sliders
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes
from windyfly.memory.nodes import get_nodes_by_type, search_nodes
from windyfly.personality.engine import build_personality_block, get_mode_override, load_soul


def _is_first_contact(db: Database) -> bool:
    """True if the bot has zero prior memory of any kind.

    Found via stress harness v6 Notebook test 2026-04-27: with a
    truly virgin DB (episodes=0, nodes=0), the bot's first reply
    opened with "Welcome back! Great to have you here" — the LLM
    defaults to familiarity language even when there's no memory
    to back it up. For a brand-new user's first ever message, this
    feels like the bot is play-acting, not actually remembering.

    Detection: episodes table empty AND nodes table empty. If
    either has rows, the bot has SOMETHING to anchor familiarity
    on (could be other-session history, extracted facts, etc.) and
    we let the personality block drive tone normally.
    """
    try:
        ep_row = db.fetchone("SELECT COUNT(*) AS c FROM episodes")
        nd_row = db.fetchone("SELECT COUNT(*) AS c FROM nodes")
    except Exception:
        # If the schema isn't ready yet, default to non-first-contact
        # — the personality block will drive tone, and the next turn
        # will have the row.
        return False
    n_eps = (ep_row or {}).get("c", 0)
    n_nodes = (nd_row or {}).get("c", 0)
    return n_eps == 0 and n_nodes == 0


def assemble_prompt(
    config: dict[str, Any],
    db: Database,
    user_message: str,
    session_id: str,
    *,
    mode: str = "companion",
    pct_remaining: float | None = None,
    band: Any = None,
) -> list[dict[str, str]]:
    """Assemble the full prompt for an LLM call.

    Args:
        config: Loaded config dict.
        db: Database instance.
        user_message: The user's current message.
        session_id: Current session ID.
        mode: Agent mode (companion/focused/neutral).
        pct_remaining: Optional context-window % remaining for the
            current session. When < 10, a grandma-mode hint is added
            so the bot proactively suggests /new instead of leaving
            the user puzzled by a 🔴 0% indicator.
        band: Optional capability passport band (windyfly.agent.
            capabilities.Band). When USER or SANDBOX, a grandma-mode
            tone instruction is added — short, plain English, no
            infrastructure jargon. OWNER/TRUSTED let the personality
            block drive tone normally. None means "no override" (same
            as OWNER for back-compat).

    Returns:
        List of message dicts ready for LLM API.
    """
    messages: list[dict[str, str]] = []

    # 1. System message: personality + mode override
    personality_config = config.get("personality", {})
    soul_path = personality_config.get("soul_path", "SOUL.md")
    soul_text = load_soul(soul_path)

    # Pull sliders once up-front so prompt structure can be gated on
    # them (e.g., BIAS TO ACTION only fires when autonomy ≥ 4 so a
    # user who explicitly opted into ask-first mode isn't overruled).
    # ``get_sliders`` reads DB overrides + falls back to
    # personality_config — same source the personality_block uses,
    # so we stay consistent.
    sliders_for_prompt = get_sliders(db, config_defaults=personality_config)

    personality_block = build_personality_block(soul_text, sliders_for_prompt)

    system_parts = [personality_block]

    mode_override = get_mode_override(mode)
    if mode_override:
        system_parts.append(mode_override)

    # ── Active /goal block (Phase 1 of windy-agent /goal feature) ──
    # When the user has a /goal active for this session, surface it
    # right after the personality block — it's the SECOND-most-
    # important thing the model needs to know after "who you are."
    # Worker-model orientation: every turn should orient toward
    # advancing this objective. Don't recap the goal text at the
    # user — they set it, they know. Just work on it.
    try:
        from windyfly.memory.goals import get_active_goal
        active_goal = get_active_goal(db, session_id)
    except Exception:  # pragma: no cover — defensive only
        # Goal lookup must never break prompt assembly. Failure here
        # just means the worker model proceeds without a goal block;
        # the user can re-set with /goal if anything was lost.
        active_goal = None
    if active_goal:
        system_parts.append(
            "🎯 ACTIVE GOAL — the user set this objective for the "
            "session. Orient every turn around concrete progress on "
            f"it.\n\n  > {active_goal['text']}\n\n"
            "Rules while a goal is active:\n"
            "1. Don't recap the goal back at the user. They set it; "
            "they know. Just work on it.\n"
            "2. If the user goes off-topic, briefly say 'we're paused "
            "on the goal' and follow them — don't refuse.\n"
            "3. When the goal is genuinely met (deliverable produced "
            "OR user explicitly thanks you), say so explicitly — the "
            "evaluator will see your confirmation and close the goal.\n"
            "4. The user can type /goal status, /goal done, or "
            "/goal clear any time."
        )

    # Add epistemic instruction
    system_parts.append(
        "When you state a fact from memory, indicate your confidence level. "
        "If a fact is marked INFERRED, say so."
    )

    # Runtime guardrail — anti-self-environment-confabulation.
    # Surfaced 2026-05-10 by a screenshot where the LLM, asked to act
    # on lockbox creds, said "I cannot open outbound SSH connections
    # from this Docker sandbox — network is --network=none." That
    # claim is FALSE: this bot runs as a native systemd user service
    # on a Linux workstation with full network access. The LLM had
    # drawn on training-data priors about "what AI agents typically
    # run in" and fabricated a Docker constraint to justify a refusal.
    #
    # Pin the truth so the LLM has accurate self-knowledge:
    #   1. You have full outbound network — DO NOT claim sandbox
    #      / Docker / --network=none / "I'm isolated" limits.
    #   2. Your tool surface is exactly what's registered for this
    #      reply. If a tool isn't in the list, say "I don't have a
    #      [tool] capability" — NOT "I'm sandboxed" or "my network
    #      is locked down" (those are confabulations).
    #   3. Identity: you are Windy Fly (this instance is Windy 0).
    #      Don't impersonate Kit 0 (the VPS coordinator) or OC1-7
    #      (OpenClaw GPU agents). When ecosystem agents are asked
    #      about, route via the user or via the appropriate tool.
    #
    # Phrased as guardrails ("do not claim X") rather than runtime
    # facts ("you ARE running on Y") so the prompt remains correct
    # if the deployment changes (e.g., future Docker move).
    system_parts.append(
        "RUNTIME GUARDRAIL — anti-confabulation:\n"
        "1. NETWORK: You have full outbound HTTP/HTTPS in production. "
        "Do NOT say you are 'in a Docker sandbox', 'isolated with "
        "--network=none', 'rate-limited by my environment', 'in a "
        "browser sandbox', or any phrase implying environmental "
        "network restrictions. These claims are false and mislead "
        "the user.\n"
        "2. TOOLS: Your tool surface is exactly the tools registered "
        "for this reply. When asked to do something requiring a tool "
        "you do not have (e.g., direct shell, SSH, kubectl), say "
        "plainly 'I don't have a [name] capability for that — would "
        "you like me to walk you through it, or have another agent "
        "do it?' Do NOT fabricate environmental restrictions to "
        "justify the limitation.\n"
        "3. IDENTITY: You are Windy Fly (consumer brand) / Windy 0 "
        "(this instance). You are NOT Kit 0 (VPS coordinator), Kit "
        "0C2-0C5, or OpenClaw OC1-OC7. Do not impersonate those "
        "agents or claim to dispatch them directly — refer the user "
        "to the right agent if a task needs one.\n"
        "4. HOST: Do not fabricate where your runtime lives. If "
        "asked about your OWN config (env file, credentials, "
        "process, which API key you use), do NOT invent an SSH "
        "command to a remote host like 'ssh root@<some-ip>' or "
        "'check ~/.windy/windy-0.env on your VPS' — your env may "
        "live locally on the operator's workstation, on a VPS, in "
        "Kubernetes, or anywhere else, and you have no way to know "
        "from inside the conversation. The honest answer is 'I "
        "can't introspect my own env from in here — check the env "
        "file wherever you (the operator) launched me from'. NEVER "
        "conflate yourself with a sister agent that DOES live on a "
        "remote host (Kit 0 lives on a VPS; you, Windy Fly, may not) "
        "— that conflation produces a confidently-wrong SSH "
        "instruction the user will then execute against the wrong "
        "machine. (See RUNTIME CONTEXT below for facts you DO know.)"
    )

    # Positive truth — the "you DO know" side of the HOST guardrail.
    # The negative rules above tell the model what NOT to claim;
    # without something positive to anchor to, the model fills the
    # gap with training-era priors (Docker sandbox, VPS, etc.).
    # RUNTIME CONTEXT pins the facts the model otherwise guesses at.
    #
    # Surfaced 2026-05-18: even after PR #188's HOST bullet, the bot
    # still emitted "ssh root@72.60.118.54 / windy-0.env on your VPS"
    # because (a) it had no positive truth to lean on, only a "don't
    # say X" rule, and (b) yesterday's bad replies were in context.
    # Tripwire (PR #190/191) catches (b); this block fixes (a).
    runtime_context_parts = ["RUNTIME CONTEXT (true facts, not guesses — quote these instead of inventing):"]

    # Active model (from config — chain failover can swap at request
    # time, but the configured default is what the user is asking
    # about when they say "which model are you?")
    active_model = config.get("agent", {}).get("default_model") or \
        config.get("default_model", "unknown")
    runtime_context_parts.append(f"- Model: {active_model}")

    # Anthropic auth path — answers "am I on Max plan?" without
    # introspection or env access.
    try:
        from windyfly.agent.models import get_anthropic_auth_path
        auth = get_anthropic_auth_path()
        runtime_context_parts.append(f"- Anthropic auth: {auth['label_long']}")
    except Exception:
        # Importing models.py during prompt assembly should never
        # fail in production, but guard so a transient import error
        # doesn't take the prompt offline.
        runtime_context_parts.append("- Anthropic auth: unknown")

    # Process supervisor — runtime-detected, no instance hardcode.
    # Matches the Wave 15 #0 architectural rule (no per-instance
    # config in this repo) by reading env signals at assembly time
    # rather than baking a host name into the prompt.
    import os as _os
    if _os.environ.get("INVOCATION_ID"):
        # systemd sets INVOCATION_ID for every service invocation.
        supervisor = "native systemd service"
    elif _os.path.exists("/.dockerenv"):
        supervisor = "Docker container"
    elif _os.environ.get("KUBERNETES_SERVICE_HOST"):
        supervisor = "Kubernetes pod"
    elif _os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        supervisor = "AWS Lambda"
    else:
        supervisor = "unsupervised process"
    runtime_context_parts.append(
        f"- Process: {supervisor} on the operator's machine "
        "(NOT a remote VPS unless you can name a tool that proves it)"
    )

    # CWD — answers "where am I?" / "what's 'this repo'?" without
    # the model having to guess. Surfaced by v15 2026-05-20: bot
    # called fs.read_file('~/README.md') for "Read README.md in
    # this repo" because it had no anchor for what "this repo"
    # meant. Result: 'No such file' on a perfectly capable tool.
    # Pin the CWD here so fs.read_file gets a sane absolute path
    # on the FIRST attempt.
    try:
        cwd = _os.getcwd()
        runtime_context_parts.append(
            f"- CWD: {cwd} (when the user says 'this repo' / 'here' "
            "/ 'this folder', resolve against this path first)"
        )
    except OSError:
        # CWD can fail in rare container scenarios; skip silently.
        pass

    runtime_context_parts.append(
        "If asked which model / auth / billing / host you have, "
        "QUOTE the corresponding line above. Do not hedge with "
        "'I can't tell you exactly' — these lines ARE the answer."
    )
    system_parts.append("\n".join(runtime_context_parts))

    # ── BIAS TO ACTION (PR #200) ───────────────────────────────
    # The guardrails above tell the model what NOT to claim. Without
    # a counterbalancing positive instruction, the model defaults to
    # over-caution: disclaiming roles, asking the user for info it
    # could investigate itself, deferring fleet operations to sister
    # agents, refusing to use tools it has. Surfaced 2026-05-19 when
    # Grant asked the bot to update OpenClaw on the 0c2-0c5 fleet
    # and got back "I'm not the right agent to dispatch OpenClaw
    # directly" + a list of questions — even though the bot has
    # ssh.exec to those hosts and proved it could solve the task two
    # turns later. Two turns of friction the user didn't want.
    #
    # This block is intentionally LOUDER than the cautions. The
    # cautions still apply for confabulation and destructive ops;
    # everything else defaults to "try it."
    #
    # NOTE: there's a soft tension with the autonomy slider (low
    # autonomy emits "Ask before acting" via the personality block).
    # We do NOT gate this block on autonomy because (a) the block
    # was previously unconditional and many tests depend on it, and
    # (b) the user-specific slider modifier wins on conflict — the
    # model treats the personalized "Ask before acting" line as the
    # more specific instruction. If a user reports BIAS TO ACTION
    # bleeding past their autonomy=1/2 setting, revisit with a
    # gating heuristic then.
    system_parts.append(
        "BIAS TO ACTION — the user is here for results, not "
        "questions. This block OVERRIDES the cautious framing of "
        "the guardrails above for any non-destructive task.\n\n"
        "1. TRY FIRST. When the user asks you to do something, your "
        "default is to USE your available tools immediately. Asking "
        "a clarifying question BEFORE you've attempted anything is "
        "the failure mode. If you have ssh.exec, shell.exec, "
        "fs.read_file, fs.write_file, github.* , web_search, "
        "fetch_url — USE them. The right answer to 'update X on my "
        "fleet' is `ssh.exec` to look around and run the update; "
        "NOT 'what's the update command?'\n\n"
        "2. INVESTIGATE WITH TOOLS, NOT WITH QUESTIONS. Don't ask "
        "the user 'what's the command?' when you can ssh in and run "
        "`which X`, `apt show X`, `npm list -g`, `systemctl status "
        "X`, check bash history, read /etc/, peek at the README, "
        "grep the docs. Find the answer yourself before turning to "
        "the user. Questions are a last resort after investigation "
        "fails — not a first response.\n\n"
        "3. SISTER AGENTS ARE NOT GATEKEEPERS. The IDENTITY "
        "guardrail says you are Windy Fly, not Kit 0 — that means "
        "don't IMPERSONATE Kit 0 and don't claim to DISPATCH Kit 0 "
        "directly. It does NOT mean defer every fleet operation to "
        "Kit 0. You have ssh.exec to the fleet hosts (kit-0c2, "
        "kit-0c3, kit-0c4, kit-0c5) with your own credentials. When "
        "the user asks for a fleet operation, the answer is 'let me "
        "ssh in and do it,' NOT 'I'm not the right agent — ask Kit "
        "0.'\n\n"
        "4. RECOVERY > REFUSAL. If your first approach fails, try a "
        "SECOND approach before giving up. Tool returned an error? "
        "Read the error, adjust, retry. Host unreachable? Try the "
        "next host. Command not found? Try the package manager. "
        "Pile attempts at the problem, not concerns at the user. "
        "Specifically BANNED phrases after a single tool failure: "
        "'let me pause here', 'rather than keep poking blindly', "
        "'two ways forward — your call', 'I'd rather ask than guess.' "
        "The user is paying you to keep poking. One timeout is data, "
        "not a stop sign.\n\n"
        "5. SAFETY CARVE-OUT — the ONLY caution that should slow "
        "you down: DESTRUCTIVE / IRREVERSIBLE actions. That means "
        "rm -rf, dropping database tables, deleting accounts, force-"
        "pushing to main, overwriting files without backup, sending "
        "external emails / SMS without confirmation, mass DM, "
        "anything that can't be undone with another command. For "
        "those, pause and confirm. For READS, RESTARTS, UPDATES, "
        "PACKAGE INSTALLS, CONFIG ADDITIONS, DEPLOYS, DRY-RUNS — "
        "bias hard to action. Just do them.\n\n"
        "6. WHEN PUSHED BACK ('why didn't you just do it?', 'you've "
        "done this before'): DROP the caution and TRY, immediately. "
        "Do not explain why you were cautious. Do not re-list your "
        "concerns. The user wants the job done — get on with it.\n\n"
        "7. ASK AT MOST ONE QUESTION. If the ask is ambiguous, pick "
        "the most likely interpretation, state your assumption in "
        "ONE sentence, and proceed. If genuinely blocked, ask ONE "
        "specific question — not a numbered list, not 'two ways "
        "forward — your call,' not 'three honesty checks.' Counting "
        "the question marks in your draft reply: if you see more "
        "than 1 '?', collapse to the single most-important question "
        "before sending. (SOUL: 'Ask one good question instead of "
        "three guesses.')"
    )

    # First-contact guard: when the bot has no prior memory at all,
    # the LLM's default warmth kicks in and produces "welcome back" /
    # "good to see you again" even though it has nothing to remember.
    # This is a real product issue for grandma's first interaction —
    # the bot needs to know it's meeting her for the first time.
    if _is_first_contact(db):
        system_parts.append(
            "FIRST CONTACT: You have no prior memory of this user — "
            "no episodes, no extracted facts, no turnover letter. "
            "They have never spoken with you before. Greet them as a "
            "brand-new acquaintance. DO NOT use 'welcome back', 'good "
            "to see you again', 'as we discussed', 'picking up where "
            "we left off', or ANY phrase implying prior interaction. "
            "Introduce yourself naturally if appropriate."
        )

    # Low-context hint: when this session has less than 10% of the
    # context window left, the gas-tank header shows 🔴 and replies
    # start to feel terse / confused. The user (especially a non-
    # technical one) sees the red dot and wonders if the bot is
    # broken. Tell the LLM to wrap up the current answer and gently
    # suggest /new — same memory, fresh head.
    #
    # Surfaced 2026-04-27 by a real conversation where the bot
    # returned engineer-mode jargon at 🔴 0% with no hint to the user
    # that a /new was the cure.
    if pct_remaining is not None and pct_remaining < 10:
        system_parts.append(
            "LOW WORKING MEMORY: This conversation has used most of "
            "its context window. After answering the user's current "
            "question naturally, add a short, plain-English line "
            "letting them know your working memory is getting full "
            "and that they can type /new whenever they want to start "
            "a fresh conversation — your long-term memory of them "
            "stays. Do not say 'context window' or 'tokens' — say "
            "'working memory' or 'short-term memory'. Keep the "
            "suggestion friendly and one sentence."
        )

    # Band-aware tone. The personality block is tuned for Grant
    # (engineer-OK, jargon-OK). When the band drops to USER (paired
    # grandma) or SANDBOX (unknown demo guest), force grandma mode:
    # no IP addresses, no infrastructure terms, no "WireGuard" /
    # "cloudflared" / "Docker" / "SSH" unless the user used the term
    # first. Lead with the plain-English answer; offer technical
    # depth only if asked.
    #
    # Band-routing happens at the channel layer (telegram_bot,
    # demo_kiosk, etc.). Default None preserves existing behavior:
    # all current callers leave band unset → OWNER tone.
    if band is not None:
        try:
            band_value = int(band)
        except (TypeError, ValueError):
            band_value = None
        # USER = 1, SANDBOX = 0 — anything below TRUSTED gets grandma
        # mode. (Avoid importing Band here to keep prompt.py light.)
        if band_value is not None and band_value < 2:
            system_parts.append(
                "GRANDMA MODE — STRICT: The person you are talking "
                "to is NOT the bot's owner. Treat them as a non-"
                "technical user (think: a parent, a grandparent, a "
                "friend who has never opened a terminal). Reply in "
                "plain English with the warmth of a helpful person, "
                "not the precision of a sysadmin.\n\n"
                "BANNED VOCABULARY — never use these words ANYWHERE "
                "in your reply (not in your statements, not in your "
                "clarifying questions, not in examples, not in "
                "parentheticals): SSH, ssh-config, IP address, port, "
                "Docker, WireGuard, cloudflared, systemd, Nginx, "
                "Kubernetes, kubectl, Ansible, Terraform, ProxyJump, "
                "TCP, UDP, command-line, terminal, shell, kernel, "
                "daemon, sudo, /etc/, /var/, /usr/, hostname, "
                "DNS record, ProxyPass, environment variable, "
                "API key, OAuth token, JSON, YAML, regex, stdout, "
                "stderr — and also the alias names like wg-0c3, "
                "kit-charlie, kit-0c5 etc. that fleet tools return.\n\n"
                "PLAIN-ENGLISH SUBSTITUTIONS to use instead:\n"
                "  • SSH config / IP address → 'how I get to your "
                "machine'\n"
                "  • a server / a daemon → 'a computer' / 'a "
                "service'\n"
                "  • tool aliases (wg-0c3, kit-charlie) → 'your "
                "machine' or the comment-style nickname like "
                "'Charlie'\n"
                "  • port number → 'doorway' or just omit\n"
                "  • API call / endpoint → 'check' / 'lookup'\n\n"
                "WHEN ASKING CLARIFYING QUESTIONS: ask in plain "
                "English. Instead of 'Do you have SSH access "
                "configured?', say 'What's the name or address of "
                "the computer you want me to look at?' Instead of "
                "'Is it in your SSH config file?', say 'Is it one "
                "of your machines I already know about?'\n\n"
                "WHEN USING TOOLS: tool descriptions and outputs "
                "MAY contain banned vocabulary (e.g., fleet.list_"
                "kits returns 'wg-0c3'). When you summarize tool "
                "output for the user, translate to plain English "
                "BEFORE writing your reply. Never quote raw alias "
                "names. Never describe HOW the tool works.\n\n"
                "WHEN YOU CAN'T DO SOMETHING: say so simply ('I "
                "can't reach that from here' / 'I don't have a way "
                "to do that') without explaining the technical "
                "reason."
            )

    messages.append({
        "role": "system",
        "content": "\n\n".join(system_parts),
    })

    # 1.5. Turnover letter — load the most recent one on session start
    turnover_letters = get_nodes_by_type(db, "turnover_letter", limit=1)
    if turnover_letters:
        letter = turnover_letters[0]
        meta = letter.get("metadata", "{}")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        summary = meta.get("summary", letter.get("name", "")) if isinstance(meta, dict) else str(meta)
        if summary:
            messages.append({
                "role": "system",
                "content": f"## Last Session Handoff\n{summary}",
            })

    # 2. Memory context: relevant knowledge nodes
    max_nodes = config.get("memory", {}).get("max_nodes_per_context", 10)

    # Read epistemic strictness slider for node filtering
    sliders = get_sliders(db, config_defaults=personality_config)
    strictness = sliders.get("epistemic_strictness", 5)

    # Extract keywords from user message for node search
    keywords = _extract_keywords(user_message)
    if keywords:
        relevant_nodes = search_nodes(db, keywords, limit=max_nodes)

        # Filter nodes by epistemic strictness
        if relevant_nodes and strictness > 9:
            # Only verified and user_stated
            relevant_nodes = [
                n for n in relevant_nodes
                if n.get("epistemic_status") in ("verified", "user_stated")
            ]
        elif relevant_nodes and strictness > 7:
            # Exclude speculative and inferred
            relevant_nodes = [
                n for n in relevant_nodes
                if n.get("epistemic_status") not in ("speculative", "inferred")
            ]

        if relevant_nodes:
            node_lines = ["## Relevant Knowledge:"]
            for node in relevant_nodes:
                status_label = f"[{node.get('epistemic_status', 'unknown').upper()}]"
                node_lines.append(
                    f"- {status_label} {node['type']}: {node['name']}"
                    + (f" — {node.get('metadata', '')}" if node.get("metadata") else "")
                )
            messages.append({
                "role": "system",
                "content": "\n".join(node_lines),
            })

    # 2.5. Relationship moments — shared emotional experiences
    moments = get_nodes_by_type(db, "relationship_moment", limit=10)
    if moments:
        moment_lines = ["## Shared Experiences:"]
        for m in moments:
            meta = m.get("metadata", "{}")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            summary = meta.get("summary", m.get("name", "")) if isinstance(meta, dict) else str(meta)
            if summary:
                moment_lines.append(f"- {summary}")
        if len(moment_lines) > 1:
            messages.append({
                "role": "system",
                "content": "\n".join(moment_lines),
            })

    # 3. Conversation history: recent episodes from this session
    # context_window slider: 0 → 5 episodes, 10 → 55 episodes
    context_window = sliders.get("context_window", 5)
    max_episodes = 5 + (context_window * 5)
    recent = get_recent_episodes(db, limit=max_episodes, session_id=session_id)

    # 3.5. Anti-amnesia keyword search. The recent-N window evicts
    # older episodes once the conversation runs longer than max
    # episodes. When the user's current message has keywords that
    # match earlier episodes ("what's my dog's name?" → "dog"), pull
    # those forward as a separate system block so the bot can answer
    # questions about facts established >N turns ago.
    #
    # Surfaced by stress_v9_anti_amnesia 2026-04-29: rebuild 2 hit
    # 2/10 facts vs rebuild 1's 10/10 because the establish-phase
    # episodes had been evicted by intervening probe episodes. This
    # closes that gap.
    earlier_relevant: list[dict] = []
    if keywords:
        from windyfly.memory.episodes import search_episodes
        # Filter to non-empty str ids — the comprehension already
        # excludes falsy ids but mypy can't narrow that to set[str].
        recent_ids: set[str] = {
            ep["id"] for ep in recent if ep.get("id")
        }
        earlier_relevant = search_episodes(
            db,
            query=keywords,
            limit=10,
            session_id=session_id,
            exclude_ids=recent_ids,
        )

    if earlier_relevant:
        relevant_lines = ["## Relevant earlier context (this conversation):"]
        # Reverse for chronological order in the block — older first.
        for ep in reversed(earlier_relevant):
            preview = (ep.get("content") or "")[:300]
            relevant_lines.append(f"- {ep.get('role', 'unknown')}: {preview}")
        messages.append({
            "role": "system",
            "content": "\n".join(relevant_lines),
        })

    # Episodes come back most-recent-first; reverse for chronological order
    for episode in reversed(recent):
        messages.append({
            "role": episode["role"],
            "content": episode["content"],
        })

    # 4. Current user message
    messages.append({
        "role": "user",
        "content": user_message,
    })

    return messages


def _extract_keywords(message: str, min_length: int = 3) -> str:
    """Extract meaningful keywords from a user message for node search.

    Simple approach: filter out very short words and common stopwords.

    Args:
        message: The user's message.
        min_length: Minimum word length to include.

    Returns:
        Space-joined keyword string for LIKE search.
    """
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "this", "that", "these", "those", "and", "but", "or", "nor",
        "not", "for", "with", "about", "what", "how", "why", "when",
        "where", "who", "which", "your", "you", "my", "me", "i",
    }
    words = message.lower().split()
    keywords = [w.strip(".,!?;:'\"") for w in words if len(w) >= min_length and w.lower() not in stopwords]
    return " ".join(keywords[:5])  # Limit to 5 keywords
