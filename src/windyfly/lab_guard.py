"""Lab guard: a test run may only ever act as a TEST agent, never as a person's real agent or on a person's keys.

Found 11-04 (Mind + Hub): the weekly QA battery on Windy 0 sourced the lab agent's env but not its state folder, so
windyfly picked up ZERO's registered key from ~/.windy, minted tokens AS ZERO, and spent Grant's own provider keys (29
calls). This guard runs before any lab harness makes a call, and refuses (exit 2, names only, never a value) unless:

- the passport is a test one (ET26-TEST-*);
- the passport token, if any, is for that same passport (its ``sub``);
- the registered agent key, if any, was registered for that same passport, and doesn't live in the real agent's state
  folder;
- the env file windyfly would write to is not the real agent's;
- no direct provider key or Claude login is readable (every model call goes through Windy Mind, as the test agent).

``--mint`` also mints one fresh mode-B token (a dry run, no model call) and checks its ``sub``.

    python -m windyfly.lab_guard [--mint]

The real agent's files default to Windy 0's (~/.windy/windy-0.env, ~/.windy); override with WINDY_LAB_GUARD_REAL_ENV
and WINDY_LAB_GUARD_REAL_STATE.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

TEST_PREFIX = "ET26-TEST-"
PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_OAUTH_ACCESS_TOKEN", "OPENAI_API_KEY",
                 "GROK_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
                 "GROQ_API_KEY", "OPENROUTER_API_KEY")


def _real_env() -> Path:
    return Path(os.environ.get("WINDY_LAB_GUARD_REAL_ENV") or Path.home() / ".windy" / "windy-0.env").expanduser()


def _real_state() -> Path:
    return Path(os.environ.get("WINDY_LAB_GUARD_REAL_STATE") or Path.home() / ".windy").expanduser()


def _real_claude() -> Path:
    """The real person's Claude login: beside the real agent's state folder (~/.claude next to ~/.windy)."""
    return Path(os.environ.get("WINDY_LAB_GUARD_REAL_CLAUDE") or _real_state().parent / ".claude" / ".credentials.json")


def _readable(path: Path) -> bool:
    try:
        with path.expanduser().open("rb"):
            return True
    except OSError:
        return False


def _same(a: Path, b: Path) -> bool:
    try:
        return a.expanduser().resolve() == b.expanduser().resolve()
    except OSError:
        return False


def _sub(token: str) -> str:
    if token.count(".") != 2:
        return ""
    seg = token.split(".")[1]
    try:
        sub = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))).get("sub")
    except (ValueError, AttributeError):
        return ""
    return sub if isinstance(sub, str) else ""


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


def problems() -> list[str]:
    """Every reason this process may NOT run as a lab agent. Empty = safe. Names only, never a value."""
    from windyfly.eternitas import agent_keys
    from windyfly.eternitas.ept_refresh import resolve_env_file

    out: list[str] = []
    passport = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    if not passport.startswith(TEST_PREFIX):
        out.append("ETERNITAS_PASSPORT is not a test passport (ET26-TEST-*)")
    token = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip() or os.environ.get("WINDY_PASSPORT_EPT", "").strip()
    if token and _sub(token) != passport:
        out.append("the passport token is for another passport")
    cred = agent_keys.credentials_path()
    if _inside(cred, _real_state()):
        out.append("the agent key file is the real agent's (its state folder)")
    entry: dict[str, Any] = agent_keys.active_entry(agent_keys.load_credentials()) or {}
    if entry.get("registered_at") and str(entry.get("passport") or passport) != passport:
        out.append("the registered agent key belongs to another passport")
    env_file = resolve_env_file()
    if env_file is not None and _same(env_file, _real_env()):
        out.append("the env file windyfly would write is the real agent's")
    if os.environ.get("WINDY_ENV_FILE") and _same(Path(os.environ["WINDY_ENV_FILE"]), _real_env()):
        out.append("WINDY_ENV_FILE is the real agent's")
    for name in PROVIDER_KEYS:
        if os.environ.get(name, "").strip():
            out.append(f"{name} is set (a person's own provider key)")
    claude = Path(os.environ.get("WINDY_CLAUDE_CREDENTIALS_PATH") or Path.home() / ".claude" / ".credentials.json")
    if _readable(claude):
        out.append("a Claude login file is readable")
    # The lab runs as the same OS user: a person's real login stays readable unless the unit hides it
    # (systemd InaccessiblePaths=). Hub on #711.
    if _readable(_real_claude()):
        out.append("the real person's Claude login is readable by this process")
    if _readable(_real_env()):
        out.append("the real agent's env file is readable by this process")
    return out


def dry_run_mint() -> str | None:
    """One fresh mode-B mint (no model call). None = its sub is this test passport; else why not."""
    from windyfly.eternitas import agent_keys

    passport = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    if not agent_keys.has_registered_key():
        return None if _sub(os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")) == passport else "no key and no token"
    try:
        token = agent_keys.request_agent_token("windy-mind", min_ttl=10 ** 9)["token"]
    except Exception as exc:  # noqa: BLE001
        return f"the dry-run mint failed ({getattr(exc, 'code', type(exc).__name__)})"
    sub = _sub(str(token))
    return None if sub == passport else "the dry-run token is for another passport"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    found = problems()
    if not found and "--mint" in argv:
        why = dry_run_mint()
        if why:
            found.append(why)
    if found:
        for p in found:
            print(f"lab guard: REFUSED: {p}", file=sys.stderr)
        return 2
    print(f"lab guard: ok, acting as {os.environ.get('ETERNITAS_PASSPORT', '').strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
