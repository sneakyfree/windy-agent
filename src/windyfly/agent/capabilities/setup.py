"""setup.* capability — LLM-callable introspection AND end-to-end
chat-driven setup flow for integrations.

Wave 4-and-a-half / grandma-mode:
  Tier 1 (this module's `setup.status`): centralized introspection so
    the LLM can ask once "what's dormant?" instead of probing each.
  Tier 2 (this module's `setup.start` + `setup.save_credential`):
    bot walks the user through getting a token, then accepts it via
    chat, validates against the live API, atomically writes it to
    the env file, AND hot-loads it into ``os.environ`` so the running
    process picks it up without a restart.

End-to-end Cloudflare grandma loop (the only integration this PR
fully closes):

    User: "set up cloudflare"
    LLM:  setup.start("cloudflare") → walkthrough steps
    LLM:  relays steps in plain English
    User: pastes token in chat
    LLM:  setup.save_credential("cloudflare", "<token>") →
            1. validates token via cloudflare.list_zones
            2. writes CLOUDFLARE_API_TOKEN=<token> to env file (atomic)
            3. os.environ["CLOUDFLARE_API_TOKEN"] = <token>
          returns {ok: true, configured_keys: [...], zones: 21}
    LLM:  "Got it — I can see all 21 zones now. Try asking..."

Gmail / Calendar still return ``{ok: false, kind:
"oauth_required"}`` because they need a browser flow. A future Tier
3 builds the magic-link OAuth.

Security
--------

``setup.save_credential`` is ``Tier.WRITE_DESTRUCTIVE`` (TRUSTED+
band, audited). Even though the env-file write is reversible, the
blast radius (redirecting the bot's identity to someone else's
account) deserves a band gate above ``USER``. Combined with the
upfront API validation, the threat model is "operator does the right
thing" which is acceptable for a single-user bot.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.setup_status import get_setup_status

logger = logging.getLogger(__name__)


# Where the bot's runtime env vars live. Default works for the Windy 0
# systemd-user deployment; override with WINDY_ENV_FILE for other
# layouts (macOS launchd, dev shell, tests).
_DEFAULT_ENV_FILE = Path(
    os.environ.get(
        "WINDY_ENV_FILE",
        str(Path.home() / ".windy" / "windy-0.env"),
    )
)


# Per-integration walkthroughs. Returned to the LLM as structured
# data; the LLM rephrases for the user (no developer-jargon parroting,
# same contract as dormant_nudge in setup_status.py).
_WALKTHROUGHS: dict[str, dict[str, Any]] = {
    "cloudflare": {
        "integration": "cloudflare",
        "name": "Cloudflare (zones + DNS)",
        "estimated_minutes": 3,
        "method": "token_paste",
        "steps": [
            "Sign in at https://dash.cloudflare.com",
            "Click your profile icon (top right), then 'My Profile' → 'API Tokens'",
            "Click 'Create Token'",
            "Use the 'Edit zone DNS' template (easiest), or click 'Custom token' and add: Zone → Zone → Read; Zone → DNS → Edit",
            "Under 'Zone Resources', set 'Include' → 'All zones'",
            "Click 'Continue to summary' → 'Create Token'",
            "Copy the token Cloudflare shows you (it's shown ONCE — don't navigate away yet)",
            "Paste the token back to me here in chat",
        ],
        "what_to_paste_looks_like": (
            "An API token, ~40 characters, usually starting with 'cfat_' or 'cf_'."
        ),
        "after_paste_action": "setup.save_credential",
        "note_to_llm": (
            "After the user pastes the token, call setup.save_credential("
            "integration='cloudflare', value=<the-token>). I'll validate "
            "it against the Cloudflare API and persist it. Do NOT echo the "
            "token back to the user in chat (it's sensitive). Confirm "
            "success in plain English: 'I can see all your zones now' or "
            "similar."
        ),
    },
    "github": {
        "integration": "github",
        "name": "GitHub (read + write via API)",
        "estimated_minutes": 4,
        "method": "token_paste",
        "steps": [
            "Sign in at https://github.com",
            "Click your avatar (top right) → 'Settings' → scroll way down → 'Developer settings'",
            "Choose 'Personal access tokens' → 'Fine-grained tokens' → 'Generate new token'",
            "Name it 'windy-agent', set expiration to your taste (90 days is fine), pick 'All repositories' or specific ones",
            "Permissions: Repository → Contents: Read and write, Issues: Read and write, Metadata: Read",
            "Generate the token, copy it",
            "Paste it back to me here in chat",
        ],
        "what_to_paste_looks_like": (
            "A token starting with 'github_pat_' (~93 characters)."
        ),
        "after_paste_action": "setup.save_credential",
        "note_to_llm": (
            "After paste, call setup.save_credential(integration='github', "
            "value=<the-token>). Don't echo the token. Confirm with "
            "plain-English success."
        ),
    },
    "gmail": {
        "integration": "gmail",
        "name": "Gmail (sending email)",
        "estimated_minutes": None,
        "method": "oauth_required",
        "steps": [
            "Gmail uses Google's secure sign-in (OAuth) — pasting a token won't work.",
            "Tier 2 of the setup flow ships a one-tap link the user can click in chat.",
        ],
        "after_paste_action": None,
        "note_to_llm": (
            "Gmail can't be set up via paste. Tell the user the bot's "
            "operator (Grant) needs to run `windy setup-gmail` for now. "
            "Magic-link chat OAuth is coming in a future update."
        ),
    },
    "calendar": {
        "integration": "calendar",
        "name": "Google Calendar (read + create events)",
        "estimated_minutes": None,
        "method": "oauth_required",
        "steps": [
            "Google Calendar uses Google's secure sign-in (OAuth) — pasting a token won't work.",
            "The operator can run `windy setup-calendar` to authorize.",
        ],
        "after_paste_action": None,
        "note_to_llm": (
            "Calendar can't be set up via paste. Tell the user the "
            "operator can run `windy setup-calendar` for now. Magic-link "
            "chat OAuth is coming in a future update."
        ),
    },
}


# Per-integration save logic — what env var name to write, and a
# zero-cost validation hook to check the token before persisting.
def _validate_cloudflare(token: str) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Validate a Cloudflare token by calling list_zones with it."""
    from windyfly.agent.capabilities.cloudflare import _list_zones_handler
    out = _list_zones_handler(token=token, page_size=100)
    if out.get("ok"):
        return True, None, {"zones_visible": out.get("count", 0)}
    return False, out.get("error", "validation failed"), None


def _validate_github(token: str) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Validate a GitHub token by calling /user."""
    import httpx
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "windyfly-agent/0.5",
        "Authorization": f"Bearer {token}",
    }
    try:
        with httpx.Client(timeout=10.0) as c:
            resp = c.get("https://api.github.com/user", headers=headers)
    except httpx.HTTPError as e:
        return False, f"network error: {e}", None
    if resp.status_code == 200:
        login = resp.json().get("login", "?")
        return True, None, {"github_login": login}
    if resp.status_code == 401:
        return False, "GitHub rejected the token (401 unauthorized)", None
    return False, f"GitHub /user returned {resp.status_code}", None


_SAVERS: dict[str, dict[str, Any]] = {
    "cloudflare": {
        "env_var": "CLOUDFLARE_API_TOKEN",
        "validator": _validate_cloudflare,
        "value_pattern": re.compile(r"^[A-Za-z0-9_\-]{20,200}$"),
    },
    "github": {
        "env_var": "GITHUB_PAT",
        "validator": _validate_github,
        "value_pattern": re.compile(r"^[A-Za-z0-9_\-]{20,200}$"),
    },
}


def _atomic_upsert_env_var(
    env_file: Path, var_name: str, value: str,
) -> None:
    """Add or replace ``var_name=value`` in the env file, atomically.

    If the line already exists, replace it. Otherwise append. Uses
    temp + rename so a crash mid-write can't leave the file in a
    half-state. File is chmod 600 (existing or fresh).
    """
    env_file.parent.mkdir(parents=True, exist_ok=True)
    new_line = f"{var_name}={value}\n"
    existing = env_file.read_text() if env_file.exists() else ""
    pattern = re.compile(rf"^{re.escape(var_name)}=.*\n?", re.MULTILINE)
    if pattern.search(existing):
        new_text = pattern.sub(new_line, existing, count=1)
    else:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        new_text = existing + new_line
    tmp = env_file.with_suffix(env_file.suffix + ".windy.tmp")
    tmp.write_text(new_text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, env_file)


def _start_handler(*, integration: str) -> dict[str, Any]:
    walkthrough = _WALKTHROUGHS.get(integration)
    if walkthrough is None:
        return {
            "ok": False,
            "error": (
                f"No setup walkthrough for integration {integration!r}. "
                f"Known: {sorted(_WALKTHROUGHS.keys())}."
            ),
        }
    return {"ok": True, **walkthrough}


def _save_credential_handler(
    *, integration: str, value: str,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Validate + persist a credential pasted in chat.

    Cloudflare/GitHub: validate against the live API, write to env
    file atomically, hot-load into os.environ. Gmail/Calendar:
    refuse with oauth_required because their tokens come from a
    browser flow, not a paste.
    """
    if integration in ("gmail", "calendar"):
        return {
            "ok": False,
            "kind": "oauth_required",
            "integration": integration,
            "error": (
                f"{integration!r} uses OAuth, not a pasted token. "
                "Tier 2 (magic-link chat OAuth) ships next; for now, "
                "the operator can run `windy setup-{0}` from the "
                "terminal.".format(integration)
            ),
        }
    saver = _SAVERS.get(integration)
    if saver is None:
        return {
            "ok": False,
            "error": (
                f"No save flow for integration {integration!r}. Known "
                f"savers: {sorted(_SAVERS.keys())}."
            ),
        }
    value = (value or "").strip()
    if not value:
        return {"ok": False, "error": "value is empty"}
    if not saver["value_pattern"].match(value):
        return {
            "ok": False,
            "error": (
                f"Pasted value doesn't match the expected token shape "
                f"for {integration}. Re-check what you copied."
            ),
        }

    # Validate against the live API BEFORE persisting. This catches
    # typos and revoked tokens at paste time, before the user thinks
    # setup is done.
    valid, err, extras = saver["validator"](value)
    if not valid:
        return {
            "ok": False,
            "kind": "validation_failed",
            "integration": integration,
            "error": (
                f"Token didn't validate against the {integration} API: "
                f"{err}. Try copying the token again — it may have "
                "been truncated, or the wrong scope was granted."
            ),
        }

    # Persist to env file + hot-load into running process.
    target = env_file or _DEFAULT_ENV_FILE
    try:
        _atomic_upsert_env_var(target, saver["env_var"], value)
    except OSError as e:
        return {
            "ok": False,
            "error": f"failed to write {target}: {e}",
        }
    os.environ[saver["env_var"]] = value

    # Re-read setup_status so caller sees the updated configured set.
    after = get_setup_status()

    return {
        "ok": True,
        "integration": integration,
        "env_var": saver["env_var"],
        "env_file": str(target),
        "hot_loaded": True,
        "validation": extras or {},
        "configured_keys": after["configured_keys"],
        "note_to_llm": (
            "Setup succeeded AND was hot-loaded into the running bot — "
            "no restart needed. Tell the user in plain English what's "
            "now possible (e.g. 'I can see all your Cloudflare zones "
            "now — try asking me about them'). Do NOT echo the token."
        ),
    }


def register_setup_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register setup.status, setup.start, and setup.save_credential."""
    logger.info(
        "Registering setup.* capabilities (status + chat-driven flow)"
    )

    def setup_status() -> dict[str, Any]:
        return get_setup_status()

    def setup_start(*, integration: str) -> dict[str, Any]:
        return _start_handler(integration=integration)

    def setup_save_credential(
        *, integration: str, value: str,
    ) -> dict[str, Any]:
        return _save_credential_handler(
            integration=integration, value=value,
        )

    registry.register(Capability(
        id="setup.status",
        description=(
            "Check which optional integrations (Gmail, Cloudflare, "
            "Calendar, GitHub) are connected. Returns a snapshot of "
            "configured + dormant integrations with friendly setup "
            "hints. Call this at conversation start or when the user "
            "asks 'what can you do?' so you can give an accurate "
            "answer."
        ),
        handler=setup_status,
        tier=Tier.READ_EXTERNAL,
        scope="introspection",
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ))

    registry.register(Capability(
        id="setup.start",
        description=(
            "Get the chat-driven setup walkthrough for an integration. "
            "Returns the steps the user needs to take to obtain a "
            "credential (e.g. how to create a Cloudflare API token), "
            "what the result will look like, and instructions for you "
            "(the LLM) about what to do next. After the user pastes "
            "the credential in chat, call setup.save_credential."
        ),
        handler=setup_start,
        tier=Tier.READ_EXTERNAL,
        scope="introspection",
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "integration": {
                    "type": "string",
                    "description": (
                        "Integration key: 'cloudflare', 'github', "
                        "'gmail', or 'calendar'. Use setup.status to "
                        "see all keys + which are dormant."
                    ),
                },
            },
            "required": ["integration"],
        },
    ))

    registry.register(Capability(
        id="setup.save_credential",
        description=(
            "Validate a credential pasted by the user in chat, then "
            "persist it to the bot's environment AND hot-load it into "
            "the running process. No restart needed. Currently "
            "supports cloudflare and github (token-paste). Gmail and "
            "calendar return oauth_required (Tier 3 / magic-link "
            "ships that). NEVER echo the token in your reply to the "
            "user. On success, confirm with plain English what's now "
            "possible (e.g. 'I can see all your zones now')."
        ),
        handler=setup_save_credential,
        tier=Tier.WRITE_DESTRUCTIVE,
        scope="credential_storage",
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "integration": {
                    "type": "string",
                    "description": (
                        "Integration key: 'cloudflare' or 'github' "
                        "(token-paste); 'gmail' or 'calendar' will "
                        "return oauth_required."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": (
                        "The credential value the user pasted. Sensitive "
                        "— never echo this back to the user."
                    ),
                },
            },
            "required": ["integration", "value"],
        },
    ))
