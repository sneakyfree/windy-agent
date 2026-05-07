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
