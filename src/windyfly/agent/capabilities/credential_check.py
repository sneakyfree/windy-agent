"""Does a configured credential answer? One cheap read at boot (Boss 10-10).

"Register only when configured" means configured AND the credential answers: a dead token makes every call of
its tools fail, which is noise for the model. A definitive refusal (401/403) means the tools are not registered;
anything we cannot judge (network error, timeout, 5xx) registers them as before, so a blip at boot never takes a
working integration away. The outcome is one log line (a fact), never the token. Skipped under pytest.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_S = 3.0


def answers(name: str, url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> bool:
    """True unless the service definitively refuses the token (401/403)."""
    if os.environ.get("PYTEST_CURRENT_TEST") and transport is None:
        return True
    try:
        with httpx.Client(timeout=TIMEOUT_S, transport=transport) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "windyfly"})
    except httpx.HTTPError as exc:
        logger.info("[credentials] %s: could not check at boot (%s); tools registered", name, type(exc).__name__)
        return True
    if resp.status_code in (401, 403):
        logger.warning("[credentials] %s: the configured token is refused (HTTP %d); its tools are NOT registered",
                       name, resp.status_code)
        return False
    logger.info("[credentials] %s: token answers (HTTP %d)", name, resp.status_code)
    return True
