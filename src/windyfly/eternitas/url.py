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


def resolve_eternitas_url(default: str = "") -> str:
    """Return the Eternitas base URL, canonical name preferred.

    `default` is used only when neither env var is set. The return
    value is right-stripped of trailing slashes.
    """
    canon = os.environ.get(_CANON, "")
    legacy = os.environ.get(_LEGACY, "")

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
