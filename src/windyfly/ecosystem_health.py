"""Ecosystem health check — verify connectivity to all Windy services.

Checks each configured service URL's health endpoint, measures latency,
and shows the agent's identity on each platform.
"""

from __future__ import annotations

import os
import time

import httpx

from windyfly.config import load_config


async def check_ecosystem_health() -> str:
    """Check health of all configured ecosystem services.

    Returns formatted status string for terminal display.
    """
    try:
        config = load_config()
    except FileNotFoundError:
        config = {}

    eco = config.get("ecosystem", {})
    lines = ["\U0001f310 Windy Ecosystem Status\n"]

    # ── Eternitas ──
    eternitas_url = eco.get("eternitas_url") or os.environ.get("ETERNITAS_API_URL", "")
    passport_id = os.environ.get("ETERNITAS_PASSPORT", "")
    if eternitas_url:
        status, latency = await _check_health(
            eternitas_url, "/api/v1/health", expected_service="eternitas",
        )
        if status == "ok":
            identity = f" — Passport: {passport_id}" if passport_id else ""
            lines.append(f"  Eternitas:    \u2705 Connected ({eternitas_url}) {latency}{identity}")
        else:
            lines.append(f"  Eternitas:    \u26a0\ufe0f  Unreachable ({eternitas_url} — {status})")
    elif passport_id:
        lines.append(f"  Eternitas:    \U0001f4be Local mode — Passport: {passport_id}")
    else:
        lines.append("  Eternitas:    \u274c Not configured (set ecosystem.eternitas_url in windyfly.toml)")

    # ── Windy Chat (Matrix) ──
    matrix_url = eco.get("matrix_homeserver") or os.environ.get("MATRIX_HOMESERVER", "")
    matrix_user = os.environ.get("MATRIX_BOT_USER", "")
    if matrix_url:
        status, latency = await _check_health(matrix_url, "/_matrix/client/versions")
        if status == "ok":
            identity = f" — {matrix_user}" if matrix_user else ""
            lines.append(f"  Windy Chat:   \u2705 Connected ({matrix_url}) {latency}{identity}")
        else:
            lines.append(f"  Windy Chat:   \u26a0\ufe0f  Unreachable ({matrix_url} — {status})")
    else:
        bot_token = os.environ.get("MATRIX_BOT_TOKEN", "")
        if bot_token:
            lines.append(f"  Windy Chat:   \U0001f4be Local mode — {matrix_user or 'configured'}")
        else:
            lines.append("  Windy Chat:   \u274c Not configured (set ecosystem.matrix_homeserver in windyfly.toml)")

    # ── Windy Mail ──
    mail_url = eco.get("windy_mail_url") or os.environ.get("WINDYMAIL_API_URL", "")
    mail_addr = os.environ.get("WINDYMAIL_EMAIL", "")
    if mail_url:
        status, latency = await _check_health(
            mail_url, "/api/v1/health", expected_service="windy-mail",
        )
        if status == "ok":
            identity = f" — {mail_addr}" if mail_addr else ""
            lines.append(f"  Windy Mail:   \u2705 Connected ({mail_url}) {latency}{identity}")
        else:
            lines.append(f"  Windy Mail:   \u26a0\ufe0f  Unreachable ({mail_url} — {status})")
    elif mail_addr:
        lines.append(f"  Windy Mail:   \U0001f4be Local mode — {mail_addr}")
    else:
        lines.append("  Windy Mail:   \u274c Not configured (set ecosystem.windy_mail_url in windyfly.toml)")

    # ── Windy Cloud ──
    cloud_url = eco.get("windy_cloud_url") or os.environ.get("WINDY_CLOUD_URL", "")
    if cloud_url:
        status, latency = await _check_health(
            cloud_url, "/api/v1/health", expected_service="windy-cloud",
        )
        if status == "ok":
            lines.append(f"  Windy Cloud:  \u2705 Connected ({cloud_url}) {latency}")
        else:
            lines.append(f"  Windy Cloud:  \u26a0\ufe0f  Unreachable ({cloud_url} — {status})")
    else:
        lines.append("  Windy Cloud:  \u274c Not configured")

    # ── Windy Pro ──
    pro_url = eco.get("windy_pro_url") or os.environ.get("WINDY_API_URL", "")
    if pro_url:
        status, latency = await _check_health(
            pro_url, "/api/v1/health", expected_service="windy-pro",
        )
        if status == "ok":
            lines.append(f"  Windy Pro:    \u2705 Connected ({pro_url}) {latency}")
        else:
            lines.append(f"  Windy Pro:    \u26a0\ufe0f  Unreachable ({pro_url} — {status})")
    else:
        lines.append("  Windy Pro:    \u274c Not configured")

    return "\n".join(lines)


async def _check_health(
    base_url: str,
    health_path: str,
    *,
    expected_service: str = "",
) -> tuple[str, str]:
    """Check a service health endpoint.

    Shape canary (P3-E5): when ``expected_service`` is given, the
    response body is parsed as JSON and ``{service: "<expected>"}``
    must match. This catches the case where a foreign process (nginx,
    another Node app, …) happens to be listening on the port and
    returns ``200`` to ``/health`` but is clearly not the service the
    operator thinks they're talking to.

    Returns:
        Tuple of (status, latency_str).
        status is "ok", "timeout", "wrong service", or error string.
        latency_str is like "(42ms)" on success or empty on failure.
    """
    url = base_url.rstrip("/") + health_path
    try:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if resp.status_code >= 500:
            return f"HTTP {resp.status_code}", ""

        if expected_service:
            try:
                body = resp.json()
            except (ValueError, TypeError):
                return f"wrong service (non-JSON /health)", ""
            if not isinstance(body, dict):
                return f"wrong service (non-object /health)", ""
            actual = str(body.get("service", "")).lower()
            if actual and actual != expected_service.lower():
                return f"wrong service ({actual!r} != {expected_service!r})", ""
            # Service field missing is tolerated — older builds, or
            # services that namespace it differently — we only fail
            # when it's present AND wrong.

        return "ok", f"({elapsed_ms}ms)"
    except httpx.ConnectError:
        return "connection refused", ""
    except httpx.TimeoutException:
        return "timeout", ""
    except Exception as e:
        return str(e)[:50], ""
