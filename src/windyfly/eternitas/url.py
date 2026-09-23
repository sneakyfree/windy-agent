"""Single source of truth for the Eternitas base URL.

`ETERNITAS_URL` is canonical per `eternitas/docs/trust-api.md`.
`ETERNITAS_API_URL` remains accepted so older deployments keep
working, but reading it alone emits a one-shot DeprecationWarning
that operators will see in logs — and we bake the warning into the
`windy ecosystem` health output so it's obvious even when log
aggregation is off.
"""

from __future__ import annotations

import logging
import os
import warnings

logger = logging.getLogger(__name__)

# The canonical vs. the legacy name. Resolution order: canonical
# first, legacy fallback. A deployment can set both (no warning);
# setting ONLY the legacy name emits the deprecation warning once.
_CANON = "ETERNITAS_URL"
_LEGACY = "ETERNITAS_API_URL"

_warned_about_legacy = False

# The production issuer. A default install hatches here: without it `windy go`
# on a clean machine refused ("No Eternitas issuer is configured") and never
# produced a passport (clean-machine journey test, 2026-09-23).
DEFAULT_ETERNITAS_URL = "https://api.eternitas.ai"

# ETERNITAS_URL=off (or none/disabled/0) explicitly switches Eternitas off.
_OFF_VALUES = {"off", "none", "disabled", "0", "false", "no"}

# Name of the explicit "use the local mock" opt-in (defined in provision.py;
# repeated here as a string to avoid an import cycle).
_FAKE_OPTIN = "WINDYFLY_ALLOW_FAKE_IDENTITY"


def resolve_eternitas_url(default: str = "") -> str:
    """Return the Eternitas base URL, canonical name preferred.

    `default` is used only when neither env var is set. The return
    value is right-stripped of trailing slashes.
    """
    canon = os.environ.get(_CANON, "")
    legacy = os.environ.get(_LEGACY, "")

    if canon.strip().lower() in _OFF_VALUES:
        return ""  # explicitly switched off; beats any default
    if canon:
        return canon.rstrip("/")

    if legacy:
        global _warned_about_legacy
        if not _warned_about_legacy:
            warnings.warn(
                f"{_LEGACY} is deprecated; set {_CANON} instead "
                "(per eternitas/docs/trust-api.md).",
                DeprecationWarning,
                stacklevel=2,
            )
            logger.warning(
                "%s is set but %s is not — please rename; %s is the "
                "canonical env var going forward.",
                _LEGACY, _CANON, _CANON,
            )
            _warned_about_legacy = True
        return legacy.rstrip("/")

    return default.rstrip("/") if default else ""


def reset_deprecation_warning_for_tests() -> None:
    """Test-only helper to re-arm the one-shot warning."""
    global _warned_about_legacy
    _warned_about_legacy = False


def issuer_url(config: dict | None = None) -> str:
    """The Eternitas base URL the hatch path should use.

    1. ``ecosystem.eternitas_url`` in config, then ETERNITAS_URL /
       ETERNITAS_API_URL: an explicit choice always wins.
    2. ETERNITAS_URL=off → "" (callers refuse or go local, loudly).
    3. The explicit mock opt-in (WINDYFLY_ALLOW_FAKE_IDENTITY) with nothing
       set → "" (the local mock lane, as before; tests rely on this).
    4. Otherwise the production issuer, DEFAULT_ETERNITAS_URL.
    """
    if config:
        cfg = (config.get("ecosystem") or {}).get("eternitas_url", "")
        if cfg:
            if str(cfg).strip().lower() in _OFF_VALUES:
                return ""
            return str(cfg).rstrip("/")
    if os.environ.get(_CANON, "").strip().lower() in _OFF_VALUES:
        return ""
    explicit = resolve_eternitas_url()
    if explicit:
        return explicit
    if os.environ.get(_FAKE_OPTIN, "").strip().lower() not in ("", "0", "false", "no", "off"):
        return ""
    return DEFAULT_ETERNITAS_URL


def eternitas_env_line() -> str:
    """The ETERNITAS_URL line for a generated .env (`windy go` and `windy setup`).

    Written explicitly so every later run and every read site talks to the
    issuer the hatch used. An explicit env choice (including "off") is kept;
    otherwise the production issuer.
    """
    return f"{_CANON}={os.environ.get(_CANON, '').strip() or DEFAULT_ETERNITAS_URL}"
