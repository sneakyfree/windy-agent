"""Guardian entry point — `python -m windyfly.supervisor.run_guardian`.

This is the persistent process the OS keep-alive backend (Tier 1)
launches and keeps alive. It builds a GuardianConfig from the
environment, wires the restart action to the OS backend (so the
guardian restarts the AGENT the OS-native way), and runs the blocking
watch loop forever.

Env:
  WINDY_GUARDIAN_CHANNELS   space-sep, default "telegram matrix"
  WINDY_GUARDIAN_UNITS      space-sep agent service names the guardian
                            restarts on wedge. Default per-OS:
                            Linux "windy-0@telegram windy-0@matrix",
                            else "windy-agent".
  WINDY_GUARDIAN_INTERVAL   seconds between checks (default 60)
  WINDY_GUARDIAN_FAIL_LIMIT consecutive fails before restart (default 3)
  WINDY_GUARDIAN_HB_MAX_AGE heartbeat staleness seconds (default 900)
  TELEGRAM_BOT_TOKEN        if set, adds a getMe external liveness probe
"""
from __future__ import annotations

import os

from windyfly.platform import IS_LINUX
from windyfly.supervisor.backends import get_backend
from windyfly.supervisor.guardian import GuardianConfig, run_forever


def _default_units() -> list[str]:
    if IS_LINUX:
        return ["windy-0@telegram", "windy-0@matrix"]
    return ["windy-agent"]


def _telegram_probe(token: str):
    def probe() -> tuple[bool, str]:
        import urllib.request
        import json
        try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            with urllib.request.urlopen(url, timeout=10) as r:
                body = json.loads(r.read().decode("utf-8"))
            return (bool(body.get("ok")), "getMe_ok" if body.get("ok") else "getMe_not_ok")
        except Exception as e:  # noqa: BLE001
            return (False, f"getMe_err:{type(e).__name__}")
    return probe


def build_config() -> GuardianConfig:
    channels = (os.environ.get("WINDY_GUARDIAN_CHANNELS") or "telegram matrix").split()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    return GuardianConfig(
        channels=channels,
        interval_s=float(os.environ.get("WINDY_GUARDIAN_INTERVAL", "60")),
        fail_limit=int(os.environ.get("WINDY_GUARDIAN_FAIL_LIMIT", "3")),
        heartbeat_max_age_s=float(os.environ.get("WINDY_GUARDIAN_HB_MAX_AGE", "900")),
        external_probe=_telegram_probe(token) if token else None,
    )


def build_restart_fn(units: list[str] | None = None):
    """Restart every agent unit via the OS backend. Returns True only if
    EVERY unit came back active (verified restart — the guardian keeps
    counting if a restart didn't take)."""
    backend = get_backend()
    unit_names = units if units is not None else (
        (os.environ.get("WINDY_GUARDIAN_UNITS") or "").split() or _default_units()
    )

    def restart_fn() -> bool:
        import time
        ok = True
        for u in unit_names:
            backend.restart(u)
        time.sleep(3)
        for u in unit_names:
            if not backend.is_active(u):
                ok = False
        return ok

    return restart_fn


def main() -> int:  # pragma: no cover — the blocking daemon
    cfg = build_config()
    restart_fn = build_restart_fn()
    run_forever(cfg, restart_fn)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
