"""Resurrect / lifeboat — last-resort recovery to a free local model.

The user-triggerable counterpart to PR #122's automatic chain-
exhaustion fallback. When all paid providers are dead (Anthropic
key revoked, OpenAI rate-limited, etc.), the user types
``/resurrect`` and the bot keeps talking — running on a local
Ollama model — long enough for the user to fix their credentials.

Design rules:

  - **Pure file-flag.** No LLM call. No DB write. The handler runs
    even when every API is dead.
  - **Probe before flagging.** If Ollama isn't installed/running,
    don't lie to the user about being "back" — give them the one
    install command and tell them to retry.
  - **Pick the best installed model.** Read Ollama's ``/api/tags``
    list, score against a curated preference list (3B-class instruct
    over 1B over older base; English-tuned over multilingual for
    grandma-mode demos). The "best" model is whatever the user
    already has — no auto-download (would be slow + a bad surprise
    during an emergency).
  - **No expiry.** Pause has expiry-rationale (cost). Resurrection
    doesn't. The user explicitly says ``/normal`` when paid creds
    are working again.
  - **Atomic writes** (.tmp + rename) so a torn flag can't bork the
    bot mid-resurrection. Same pattern as ``pause()``.

The agent loop reads ``is_resurrected()`` at the top of
``agent_respond`` and routes through the offline-fallback path with
the chosen model. ``offline.get_offline_response`` honors the
``WINDY_RESURRECT_MODEL`` hint we set when writing the flag.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Curated preference list ───────────────────────────────────────
#
# Ordered best-quality first within each tier. The picker walks this
# list and selects the first model that's actually installed in the
# operator's Ollama. If none match, it falls back to the largest
# installed model regardless of name.
#
# Update this list when better small-footprint models ship. Models
# named here MUST be runnable on consumer laptop hardware (2-8GB
# RAM) — we're optimizing for "grandma's bot stays talkable",
# not frontier quality. Quality wins do NOT justify a 60s per-reply
# response latency on a 2017 iMac.
PREFERRED_MODELS: tuple[str, ...] = (
    # Sweet spot: ~3B params, fast on CPU, instruction-tuned
    "llama3.2:3b",
    "llama3.2:3b-instruct",
    "qwen2.5:3b",
    "qwen2.5:3b-instruct",
    "phi3.5:3.8b",
    "phi3:3.8b",
    "gemma2:2b",
    # Smaller fallbacks for older / lower-RAM hardware
    "llama3.2:1b",
    "llama3.2:1b-instruct",
    "qwen2.5:1.5b",
    "phi3:mini",
    # Older-vintage but reliable
    "llama3:8b",
    "llama3.1:8b",
    "mistral:7b",
    # Last-resort generic name (older Ollama installs)
    "llama3.2",
    "llama3",
)


def _flag_path() -> Path:
    return Path(os.environ.get(
        "WINDY_RESURRECT_FLAG",
        "/home/grantwhitmer/.windy/.resurrected",
    ))


def is_resurrected() -> bool:
    """Quick check at the top of agent_respond. File-based — one
    stat() call, no DB."""
    return _flag_path().exists()


def resurrection_state() -> dict[str, Any]:
    """Read the resurrection flag. Returns {} if not active.

    Schema when active:
        {
          "active":         True,
          "ts":             "...",
          "model":          "llama3.2:3b",
          "previous_model": "claude-haiku-4-5-20251001",
          "actor":          "<sender_id or 'user'>",
        }
    """
    path = _flag_path()
    if not path.exists():
        return {"active": False}
    try:
        data = json.loads(path.read_text())
    except Exception:
        # Torn flag — treat as active with unknown metadata. Better
        # to keep lifeboat ON with no metadata than to silently drop
        # back to a dead-cred frontier model mid-emergency.
        return {"active": True, "model": None, "previous_model": None}
    return {"active": True, **data}


def list_installed_ollama_models(timeout: float = 2.0) -> list[dict[str, Any]]:
    """Probe Ollama's ``/api/tags`` for installed models.

    Returns a list like:
        [{"name": "llama3.2:3b", "size": 2147483648, ...}, ...]
    Empty list when Ollama isn't running or has no models. NEVER
    raises — the channel adapter relies on this for the "is Ollama
    here?" branch and a crash here would defeat the purpose."""
    try:
        import httpx
        resp = httpx.get(
            "http://localhost:11434/api/tags", timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        models = data.get("models", [])
        if not isinstance(models, list):
            return []
        return models
    except Exception as e:
        logger.debug("Ollama tags probe failed: %s", e)
        return []


def pick_best_model(installed: list[dict[str, Any]] | None = None) -> str | None:
    """Pick the best model from the operator's installed Ollama set.

    Strategy:
      1. Walk PREFERRED_MODELS in order; return the first match.
      2. If no preferred match, return the LARGEST installed model
         (size in bytes is in the /api/tags response).
      3. If Ollama has no models at all, return None — caller must
         handle the "Ollama running but empty" case.
    """
    if installed is None:
        installed = list_installed_ollama_models()
    if not installed:
        return None

    installed_names = {m.get("name", "") for m in installed if isinstance(m, dict)}

    for preferred in PREFERRED_MODELS:
        if preferred in installed_names:
            return preferred

    # No preferred match — fall back to largest installed.
    sized = [
        (m.get("size", 0), m.get("name", ""))
        for m in installed
        if isinstance(m, dict) and m.get("name")
    ]
    if not sized:
        return None
    sized.sort(reverse=True)
    return sized[0][1]


def resurrect(
    actor: str = "user",
    previous_model: str | None = None,
) -> dict[str, Any]:
    """Probe Ollama, pick best installed model, write flag.

    Returns a payload the channel adapter renders into a reply.
    Cases:
      - {"ok": True, "model": "...", ...}: lifeboat active
      - {"ok": False, "reason": "ollama_not_running", ...}
      - {"ok": False, "reason": "no_models_installed", ...}
      - {"ok": False, "reason": "flag_write_failed", ...}
    """
    installed = list_installed_ollama_models()
    if not installed:
        # Ollama not running OR not installed.
        return {
            "ok": False,
            "reason": "ollama_not_running",
            "install_hint": (
                "curl -fsSL https://ollama.com/install.sh | sh && "
                "ollama pull llama3.2:3b"
            ),
        }

    model = pick_best_model(installed)
    if model is None:
        return {
            "ok": False,
            "reason": "no_models_installed",
            "install_hint": "ollama pull llama3.2:3b",
        }

    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "previous_model": previous_model,
        "actor": actor,
    }

    path = _flag_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        tmp.replace(path)
    except Exception as e:
        logger.error("Failed to write resurrection flag: %s", e)
        return {"ok": False, "reason": "flag_write_failed", "error": str(e)}

    logger.warning(
        "RESURRECTED: actor=%s model=%s previous=%s — bot now using local Ollama",
        actor, model, previous_model,
    )

    # Clear the consecutive-Ollama-failure counter on every fresh
    # lifeboat entry. The wedged-escape check fires after 3
    # consecutive Ollama failures; without zeroing here, failures
    # from a previous lifeboat session (or from test-state bleed)
    # would carry over and instantly trip the escape on the new
    # entry. Best-effort — the counter is a single file; failure
    # to clear just means escape might fire a turn sooner than
    # intended.
    try:
        from windyfly.agent.offline import _record_ollama_outcome
        _record_ollama_outcome(success=True)
    except Exception as e:
        logger.debug("ollama counter clear failed during resurrect: %s", e)

    # Pre-load the model so the user's first chat doesn't pay the
    # 1.5-2s model-load cost on top of CPU inference. Best-effort —
    # if the warmup itself fails, the next real chat will just load
    # the model itself; we don't want a warmup failure to break the
    # lifeboat entry. Skip when WINDY_SKIP_OLLAMA_WARMUP is set
    # (used by the test suite to avoid hitting a real Ollama).
    if not os.environ.get("WINDY_SKIP_OLLAMA_WARMUP"):
        try:
            from windyfly.agent.offline import warm_ollama_model
            warm_ollama_model(model)
        except Exception as e:
            logger.debug("warm_ollama_model failed during resurrect: %s", e)

    return {"ok": True, "active": True, **payload}


def normalize() -> dict[str, Any]:
    """Clear the resurrection flag. Best-effort — missing flag is a
    no-op (returns ok:True regardless)."""
    path = _flag_path()
    existed = path.exists()
    prior_model = None
    if existed:
        try:
            prior_model = json.loads(path.read_text()).get("model")
        except Exception:
            pass
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("normalize() flag delete failed: %s", e)
        return {"ok": False, "error": str(e)}
    logger.info("NORMAL: resurrection flag cleared (existed=%s, model=%s)",
                existed, prior_model)
    return {"ok": True, "was_resurrected": existed, "prior_model": prior_model}


def current_model() -> str | None:
    """The Ollama model the bot is using under resurrection, or None
    if not resurrected. Read by ``offline.get_offline_response`` so
    we don't have to plumb the model through every call site."""
    state = resurrection_state()
    if not state.get("active"):
        return None
    return state.get("model")


# ── Auto-resurrect (PR #145) ──────────────────────────────────────
#
# When PR #122's chain-exhaustion catch fires (every paid provider
# 401'd or 5xx'd), the loop can attempt to flip into lifeboat mode
# automatically — same behavior as if the user had typed /resurrect
# manually, except we ALWAYS notify the user so the mode change is
# never silent.
#
# Three guards keep this safe:
#   1. Disable flag — user can opt-out via /auto-resurrect off
#   2. Cooldown — 60s between attempts so a rapid-fire user doesn't
#      hammer the Ollama probe on each message
#   3. Single-shot per turn — caller invokes auto_resurrect_attempt
#      ONCE per agent_respond; if it fails, fall through to the
#      standard offline_response without retrying within the same
#      turn

_AUTO_COOLDOWN_S = 60.0


def _auto_disable_flag_path() -> Path:
    """When this file exists, auto-resurrect stays OFF."""
    return Path(os.environ.get(
        "WINDY_AUTO_RESURRECT_DISABLED",
        "/home/grantwhitmer/.windy/.auto_resurrect_disabled",
    ))


def _auto_attempt_marker_path() -> Path:
    """Records the timestamp of the last auto-resurrect attempt for
    cooldown enforcement. Single line: a Unix timestamp."""
    return Path(os.environ.get(
        "WINDY_AUTO_RESURRECT_LAST",
        "/home/grantwhitmer/.windy/.auto_resurrect_last",
    ))


def is_auto_resurrect_disabled() -> bool:
    """True iff the user has opted out via /auto-resurrect off.
    Default (no flag file) is ENABLED — most grandmas won't know to
    enable manually, and the failure mode of an enabled-by-default
    auto-resurrect is "user sees a notification" not "bot crashes."
    """
    return _auto_disable_flag_path().exists()


def set_auto_resurrect(enabled: bool, actor: str = "user") -> dict[str, Any]:
    """Toggle auto-resurrect via the slash-command handler.

    Enable: delete the disable flag (if present).
    Disable: write the disable flag.

    Idempotent — calling enable twice is fine.
    """
    path = _auto_disable_flag_path()
    if enabled:
        existed = path.exists()
        try:
            path.unlink(missing_ok=True)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        logger.info("AUTO-RESURRECT enabled by %s (flag-removed=%s)", actor, existed)
        return {"ok": True, "enabled": True, "was_disabled": existed}
    # Disable
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        tmp.replace(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    logger.info("AUTO-RESURRECT disabled by %s", actor)
    return {"ok": True, "enabled": False}


def _within_auto_cooldown() -> bool:
    """True if last auto-resurrect attempt was less than
    ``_AUTO_COOLDOWN_S`` ago. Prevents zombie loops when multiple
    chain-fails come in rapid succession."""
    path = _auto_attempt_marker_path()
    if not path.exists():
        return False
    try:
        last = float(path.read_text().strip())
    except Exception:
        return False
    age = datetime.now(timezone.utc).timestamp() - last
    return age < _AUTO_COOLDOWN_S


def _mark_auto_attempt() -> None:
    """Stamp the cooldown marker. Best-effort — failure to record
    just means cooldown won't kick in (slightly more attempts)."""
    path = _auto_attempt_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(datetime.now(timezone.utc).timestamp()))
    except Exception as e:
        logger.debug("auto-resurrect mark failed: %s", e)


def is_permanent_auth_error(error_str: str | None) -> bool:
    """Classify a chain-exhaustion error string as permanent-auth
    vs. transient. Permanent-auth = the provider rejected the
    credential itself (401 invalid x-api-key, 403 permission
    denied on org). Transient = rate limit, 5xx, network blip —
    those WILL eventually clear; resurrect is the right answer
    for them. Permanent-auth WON'T clear without operator
    intervention, so resurrecting just wedges the bot in lifeboat
    while paid keeps 401-ing on every escape attempt.

    Surfaced 2026-05-20: Grant's OAuth Max token (sk-ant-oat01-…)
    expired; auto_resurrect kept firing on every 401, wedging
    lifeboat. PR #201's escape mechanism worked correctly but
    couldn't overcome the underlying permanent failure — the
    bot needed to STOP trying lifeboat and surface "your auth is
    dead" instead.

    Conservative pattern: require BOTH "401" (HTTP status) AND
    one of the auth-specific Anthropic markers ("authentication_
    error" / "invalid x-api-key" / "invalid api key"). The
    double-signal requirement avoids treating ambiguous 401s
    (e.g., from a 5xx mis-labeled by some intermediate proxy)
    as permanent. Falls back to "transient" (resurrect-OK) on
    any uncertainty — safer to enter lifeboat than to strand
    the user.
    """
    if not error_str:
        return False
    s = error_str.lower()
    has_401 = "401" in s or "403" in s
    auth_markers = (
        "authentication_error", "authentication error",
        "invalid x-api-key", "invalid api key",
        "invalid_api_key", "invalid_authentication",
        "permission_error",
        "credit balance is too low",
    )
    has_auth_marker = any(m in s for m in auth_markers)
    return has_401 and has_auth_marker


def auto_resurrect_attempt(
    actor: str = "auto",
    previous_model: str | None = None,
    error_str: str | None = None,
) -> dict[str, Any]:
    """Try to flip into lifeboat mode automatically.

    Returns:
        {"ok": True, "model": "..."} on success — bot is now
            resurrected, channel handler should prepend the user
            notification and route through offline_response.
        {"ok": False, "reason": "disabled"} when user opted out.
        {"ok": False, "reason": "cooldown"} when within 60s of
            previous attempt.
        {"ok": False, "reason": "permanent_auth_failure"} when
            ``error_str`` is classified as permanent-auth (caller
            should surface a dedicated "your auth is dead" reply
            instead of falling back to offline_response — lifeboat
            won't help and the loop would just wedge).
        {"ok": False, "reason": "ollama_not_running"} / etc. when
            the underlying ``resurrect()`` call fails.

    On success, the resurrection flag IS written and subsequent
    calls hit PR #138's resurrection short-circuit (no chain-fail
    catch needed)."""
    if is_auto_resurrect_disabled():
        return {"ok": False, "reason": "disabled"}
    # Permanent-auth short-circuit: skip lifeboat entirely when the
    # underlying failure was a credential rejection. Resurrect is
    # for transient problems; this one needs the operator to
    # refresh the token.
    if is_permanent_auth_error(error_str):
        return {
            "ok": False,
            "reason": "permanent_auth_failure",
            "error_str": (error_str or "")[:300],
        }
    if _within_post_recovery_grace():
        # We JUST climbed out of lifeboat. A chain-fail on this
        # turn is almost certainly the same flap that caused the
        # original lifeboat entry — bouncing back in immediately
        # would create a lifeboat → paid → lifeboat ping-pong.
        # Skip; let the user see one offline reply, then try again
        # after the grace window expires (5 min).
        return {"ok": False, "reason": "post_recovery_grace"}
    if _within_auto_cooldown():
        return {"ok": False, "reason": "cooldown"}

    _mark_auto_attempt()
    return resurrect(actor=actor, previous_model=previous_model)


# ── Auto-recover from resurrection (lifeboat-stuck-state fix) ─────
#
# Companion to auto_resurrect_attempt(): once we're IN lifeboat mode,
# periodically check whether the paid LLM is healthy again. If it is,
# clear the resurrect flag so the bot routes back through the paid
# (high-quality) provider. Without this, a transient paid-side blip
# (a single 401 or 5xx from chain-exhaustion catch) would strand the
# bot in slow-Ollama mode FOREVER until the user typed /normal —
# surfaced 2026-05-10 when bot sat in lifeboat for 2h replying
# "Local model error: timed out" on every chat.
#
# Cadence: 2 minutes between probes. Generous enough that we don't
# HTTP-storm api.anthropic.com on every chat, tight enough that a
# transient blip clears within a few replies.

_RECOVERY_PROBE_INTERVAL_S = 120.0

# Post-recovery grace (Risk 2 hardening):
# After a successful climb-out of lifeboat, suppress auto_resurrect
# for this many seconds so a transient paid-side flap doesn't
# immediately re-resurrect us. Without this, a flapping API key
# (e.g., Anthropic returning 200 once then 5xx) can ping-pong the
# bot lifeboat → paid → lifeboat on the SAME turn (recovery probe
# OK at step 1.7, paid call fails at step 4, chain-exhaust catch
# fires auto_resurrect_attempt whose cooldown was already
# satisfied by the original lifeboat entry minutes ago).
_POST_RECOVERY_GRACE_S = 300.0  # 5 min


def _post_recovery_grace_path() -> Path:
    """Marker stamped on successful climb-out of lifeboat. While
    fresh, ``_within_post_recovery_grace`` returns True and
    ``auto_resurrect_attempt`` short-circuits before flipping back
    into lifeboat."""
    return Path(os.environ.get(
        "WINDY_POST_RECOVERY_GRACE",
        "/home/grantwhitmer/.windy/.post_recovery_grace",
    ))


def _within_post_recovery_grace() -> bool:
    path = _post_recovery_grace_path()
    if not path.exists():
        return False
    try:
        last = float(path.read_text().strip())
    except Exception:
        return False
    age = datetime.now(timezone.utc).timestamp() - last
    return age < _POST_RECOVERY_GRACE_S


def _mark_post_recovery() -> None:
    path = _post_recovery_grace_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(datetime.now(timezone.utc).timestamp()))
    except Exception as e:
        logger.debug("post-recovery grace mark failed: %s", e)


def _paid_health_probe(timeout: float = 4.0) -> dict[str, Any]:
    """Probe whether ANY configured paid provider has a *valid* key.

    Stronger than ``offline.is_online()``: that one does a plain
    HTTP GET to api.anthropic.com and counts a 401 ("no key sent")
    as "reachable." A key-revoked bot would pass that check on
    every probe, recover, immediately re-fail on the next call,
    and ping-pong forever.

    This probe sends the actual API key against the provider's
    /v1/models endpoint:
      - 2xx → key valid → return ok=True with provider name
      - 401/403 → key DEAD → skip provider, try next
      - 5xx / timeout / connect error → retry once, then skip

    Returns ``{"ok": True, "provider": "anthropic"|"openai",
    "status": int}`` on success; ``{"ok": False, "reason": "..."}
    `` otherwise.

    Reasons: ``no_keys_configured``, ``all_keys_failed``,
    ``import_failed``.
    """
    try:
        import httpx
    except Exception as e:
        return {"ok": False, "reason": "import_failed", "detail": str(e)}

    candidates: list[tuple[str, str, dict[str, str]]] = []
    if (key := os.environ.get("ANTHROPIC_API_KEY")):
        candidates.append((
            "anthropic",
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        ))
    if (key := os.environ.get("OPENAI_API_KEY")):
        candidates.append((
            "openai",
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {key}"},
        ))
    if not candidates:
        return {"ok": False, "reason": "no_keys_configured"}

    last_status: int | None = None
    last_provider: str | None = None
    for provider, url, headers in candidates:
        for attempt in (1, 2):
            try:
                resp = httpx.get(url, headers=headers, timeout=timeout)
                last_status = resp.status_code
                last_provider = provider
                if 200 <= resp.status_code < 300:
                    return {
                        "ok": True,
                        "provider": provider,
                        "status": resp.status_code,
                    }
                if resp.status_code in (401, 403):
                    # Key dead; no point retrying THIS provider but
                    # we still try the next one.
                    break
                # 5xx or other transient — retry once.
                continue
            except Exception:
                continue
    return {
        "ok": False,
        "reason": "all_keys_failed",
        "last_status": last_status,
        "last_provider": last_provider,
    }


def _recovery_probe_marker_path() -> Path:
    """Last paid-LLM recovery probe timestamp (separate from
    _auto_attempt_marker_path; that one is for FORWARD trips into
    lifeboat, this one is for the BACKWARD trip out)."""
    return Path(os.environ.get(
        "WINDY_RECOVERY_PROBE_LAST",
        "/home/grantwhitmer/.windy/.recovery_probe_last",
    ))


def _within_recovery_probe_cooldown() -> bool:
    path = _recovery_probe_marker_path()
    if not path.exists():
        return False
    try:
        last = float(path.read_text().strip())
    except Exception:
        return False
    age = datetime.now(timezone.utc).timestamp() - last
    return age < _RECOVERY_PROBE_INTERVAL_S


def _mark_recovery_probe() -> None:
    path = _recovery_probe_marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(datetime.now(timezone.utc).timestamp()))
    except Exception as e:
        logger.debug("recovery-probe mark failed: %s", e)


def attempt_paid_recovery() -> dict[str, Any]:
    """If currently resurrected AND the paid LLM is reachable again,
    clear the flag and return a recovery notification.

    Returns:
        {"recovered": True, "prior_model": "...", "notice": "..."}
            — flag cleared, caller should prepend ``notice`` to the
            paid-LLM reply
        {"recovered": False, "reason": "not_resurrected"}
            — wasn't in lifeboat; nothing to do
        {"recovered": False, "reason": "cooldown"}
            — probed too recently; skip this turn
        {"recovered": False, "reason": "still_offline"}
            — paid probe failed; stay in lifeboat
    """
    if not is_resurrected():
        return {"recovered": False, "reason": "not_resurrected"}
    if _within_recovery_probe_cooldown():
        return {"recovered": False, "reason": "cooldown"}

    _mark_recovery_probe()

    # Real key-validity probe — much stronger than reachability.
    # See ``_paid_health_probe`` docstring for why is_online() is
    # not enough.
    probe = _paid_health_probe()
    if not probe.get("ok"):
        return {
            "recovered": False,
            "reason": "still_offline",
            "probe": probe,
        }

    # Paid LLM is healthy. Clear the flag and let the caller fall
    # through to the normal paid path.
    state = resurrection_state()
    prior_model = state.get("model")
    out = normalize()
    if not out.get("ok"):
        # Couldn't clear the flag — stay in lifeboat to be safe.
        return {"recovered": False, "reason": "normalize_failed"}

    # Stamp the post-recovery grace marker so a transient paid-side
    # flap on this VERY turn doesn't immediately bounce us back into
    # lifeboat (anti-pingpong, Risk 2 hardening).
    _mark_post_recovery()

    provider = probe.get("provider", "paid")
    notice = (
        f"✅ Recovered — {provider} is healthy again, switching back "
        f"from lifeboat mode (was using "
        f"{prior_model or 'local model'}).\n\n"
    )
    logger.info(
        "RECOVERED: %s key validated, cleared resurrect flag "
        "(prior_model=%s)", provider, prior_model,
    )
    return {
        "recovered": True,
        "prior_model": prior_model,
        "provider": provider,
        "notice": notice,
    }


def lifeboat_status() -> dict[str, Any]:
    """Comprehensive snapshot for the /lifeboat status command.

    Surfaces every piece of state a curious user (or a debugging
    operator) needs to answer "why is my bot acting weird?":
      - Are we in lifeboat right now?
      - Which Ollama model? Since when?
      - Is auto-resurrect enabled? Is it in cooldown?
      - When was the last paid-recovery probe?
      - Are we in the post-recovery grace window?

    Pure read-only — never mutates flags. Safe to call on every
    /lifeboat invocation."""
    state = resurrection_state()
    auto = auto_resurrect_status()

    recov_path = _recovery_probe_marker_path()
    recov_last_ts: float | None = None
    if recov_path.exists():
        try:
            recov_last_ts = float(recov_path.read_text().strip())
        except Exception:
            pass

    grace_path = _post_recovery_grace_path()
    grace_last_ts: float | None = None
    if grace_path.exists():
        try:
            grace_last_ts = float(grace_path.read_text().strip())
        except Exception:
            pass

    return {
        "in_lifeboat": bool(state.get("active")),
        "model": state.get("model"),
        # resurrect() writes the timestamp under "ts" (see _write_state
        # call ~line 211); reading "at" here silently returned None on
        # every lifeboat entry so the /lifeboat command's "Since:" line
        # never rendered. Caught by the FSM as-built audit in Phase
        # 2.2.1 of the launch gauntlet.
        "since": state.get("ts"),
        "actor": state.get("actor"),
        "previous_model": state.get("previous_model"),
        "auto_resurrect_enabled": auto["enabled"],
        "auto_resurrect_in_cooldown": auto["in_cooldown"],
        "auto_resurrect_last_attempt_ts": auto["last_attempt_ts"],
        "recovery_probe_last_ts": recov_last_ts,
        "recovery_probe_in_cooldown": _within_recovery_probe_cooldown(),
        "recovery_probe_interval_s": _RECOVERY_PROBE_INTERVAL_S,
        "post_recovery_grace_last_ts": grace_last_ts,
        "in_post_recovery_grace": _within_post_recovery_grace(),
        "post_recovery_grace_s": _POST_RECOVERY_GRACE_S,
    }


def format_lifeboat_status(status: dict[str, Any] | None = None) -> str:
    """Render lifeboat_status() as a Telegram-friendly multiline
    string. If ``status`` is omitted, fetches the current snapshot.
    """
    if status is None:
        status = lifeboat_status()

    lines: list[str] = []
    if status["in_lifeboat"]:
        lines.append("🛟 *Lifeboat mode: ACTIVE*")
        if status.get("model"):
            lines.append(f"  • Model: `{status['model']}`")
        if status.get("since"):
            lines.append(f"  • Since: {status['since']}")
        if status.get("actor"):
            lines.append(f"  • Triggered by: {status['actor']}")
        if status.get("previous_model"):
            lines.append(
                f"  • Was previously on: {status['previous_model']}"
            )
    else:
        lines.append("✅ *Lifeboat mode: inactive* — running on paid model")

    lines.append("")
    lines.append("*Auto-resurrect:* "
                 + ("enabled" if status["auto_resurrect_enabled"]
                    else "disabled"))
    if status["auto_resurrect_in_cooldown"]:
        lines.append("  • In 60s cooldown (recent attempt)")

    if status["in_lifeboat"]:
        lines.append("")
        lines.append("*Paid-LLM recovery probe:*")
        if status["recovery_probe_in_cooldown"]:
            lines.append(
                f"  • In cooldown (next probe in <"
                f"{int(status['recovery_probe_interval_s'])}s)"
            )
        else:
            lines.append("  • Ready — will fire on next chat")

    if status["in_post_recovery_grace"]:
        lines.append("")
        lines.append(
            f"⏳ *Post-recovery grace:* active "
            f"({int(status['post_recovery_grace_s'])}s) — "
            f"auto-resurrect is paused to prevent ping-pong."
        )

    lines.append("")
    lines.append("Commands: `/resurrect` `/normal` `/auto-resurrect on|off`")

    return "\n".join(lines)


def auto_resurrect_status() -> dict[str, Any]:
    """Read the current state of the auto-resurrect setting +
    cooldown for the /auto-resurrect status command."""
    path = _auto_disable_flag_path()
    last_path = _auto_attempt_marker_path()
    last_ts = None
    if last_path.exists():
        try:
            last_ts = float(last_path.read_text().strip())
        except Exception:
            pass
    return {
        "enabled": not path.exists(),
        "last_attempt_ts": last_ts,
        "in_cooldown": _within_auto_cooldown(),
        "cooldown_seconds": _AUTO_COOLDOWN_S,
    }
