"""Select the Eternitas client (real or the explicit local fake) for reads such as ``windy status``.

Eternitas is the ONE issuer, and windyfly never mints: every agent is born through Hub's hatch ceremony
(windyfly.hub_hatch). The old provisioning path that registered a passport from here was removed on
2026-10-21 (no second hallway).

**It does NOT fall back to the mock.** An unconfigured run raises
``FakeIdentityRefused``. Falling back silently is exactly the defect that
was removed: it minted local ``ET-LXXXX`` numbers Eternitas had never
issued and reported the hatch as a success, which is worse than failing
because it looks like it worked. Reaching the mock now takes an explicit
``WINDYFLY_ALLOW_FAKE_IDENTITY=1`` (or a ``mock://`` URL), and using it
logs a warning on every run.

If you are here to "restore the fallback for local development": don't —
set the opt-in instead. See ``00-THE-CONTRACT.md`` in the eternitas repo,
"No lane may fabricate identity."
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from rich.console import Console

from windyfly.eternitas.models import EternitasPassport

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)
console = Console()
PROJECT_ROOT = get_project_root()


# Opt-in for the local mock. Named so that seeing it in a process list, a
# systemd unit or a CI config tells you exactly what you are looking at:
# this run does not produce a real Eternitas identity.
FAKE_IDENTITY_OPTIN_ENV = "WINDYFLY_ALLOW_FAKE_IDENTITY"


class FakeIdentityRefused(RuntimeError):
    """Raised rather than silently minting a passport nobody issued."""


@dataclass
class EternitasProvisionResult:
    """Result of Eternitas provisioning during hatch."""

    success: bool
    passport: EternitasPassport | None = None
    error: str = ""


def _fake_identity_allowed() -> bool:
    value = os.environ.get(FAKE_IDENTITY_OPTIN_ENV, "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


def get_eternitas_client(db=None, config: dict | None = None):
    """Return the appropriate Eternitas client based on configuration.

    Checks ecosystem.eternitas_url from config, then the ETERNITAS_URL env var
    (with ETERNITAS_API_URL as a deprecated fallback). Uses the real HTTP
    client when a URL is set.

    Falling back to the mock takes an EXPLICIT request — either a ``mock://``
    URL or ``WINDYFLY_ALLOW_FAKE_IDENTITY``. It used to happen silently on
    any unconfigured run, which meant the ceremony went green and handed
    back a passport number Eternitas had never issued. A credential nobody
    issued is the exact thing this platform exists to prevent, and looking
    like success is worse than failing (00-THE-CONTRACT.md: "No lane may
    fabricate identity. A lane that cannot reach Eternitas fails loudly.").

    Raises:
        FakeIdentityRefused: no issuer configured and no explicit opt-in.
    """
    from windyfly.eternitas.url import issuer_url

    # Explicit config/env first, then the production issuer by default. It is ""
    # only when Eternitas is switched off (ETERNITAS_URL=off) or the explicit mock
    # opt-in is set with nothing else configured.
    api_url = issuer_url(config)

    if api_url and not api_url.startswith("mock"):
        from windyfly.eternitas.client import EternitasClient
        return EternitasClient(api_url=api_url)

    if not api_url:
        if not _fake_identity_allowed():
            raise FakeIdentityRefused(
                "Eternitas is switched off (ETERNITAS_URL=off), so this run would "
                "mint a LOCAL passport number that Eternitas has never issued and "
                "then report success. Refusing.\n"
                "  • To hatch a real identity: unset ETERNITAS_URL (the default is "
                "https://api.eternitas.ai) or set it to your issuer.\n"
                f"  • For offline development with a FAKE identity: set "
                f"{FAKE_IDENTITY_OPTIN_ENV}=1, or ETERNITAS_URL=mock://local."
            )
        logger.warning(
            "%s is set — using the LOCAL MOCK registry. Passports minted in "
            "this run are NOT real Eternitas identities and no ecosystem "
            "service will honour them.",
            FAKE_IDENTITY_OPTIN_ENV,
        )

    # Mock mode — need a database
    if db is None:
        from windyfly.memory.database import Database
        db_path = os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db")
        db = Database(db_path)

    from windyfly.eternitas.mock import MockEternitasClient
    return MockEternitasClient(db)


