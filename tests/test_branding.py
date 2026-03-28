"""Tests for windyfly.branding — fork identity configuration.

Covers both Windy Fly (ecosystem fork) and HiFly (open-source core)
brand configurations, constant completeness, and the non-negotiable
hatching ceremony flag.
"""

from __future__ import annotations

import os
from unittest.mock import patch


# ═══════════════════════════════════════════════════════════════════════
# Fork Identity
# ═══════════════════════════════════════════════════════════════════════


class TestDefaultBrand:
    def test_default_is_windy_fly(self):
        """Without HIFLY_BRAND env, the brand should be Windy Fly."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HIFLY_BRAND", None)
            # Re-import to pick up the env change
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.IS_WINDY_FLY is True
            assert b.BRAND_NAME == "Windy Fly"

    def test_hifly_brand_switch(self):
        """HIFLY_BRAND=hifly should switch to HiFly branding."""
        with patch.dict(os.environ, {"HIFLY_BRAND": "hifly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.IS_WINDY_FLY is False
            assert b.BRAND_NAME == "HiFly"
            assert b.HAS_ECOSYSTEM is False
            # Restore default
        with patch.dict(os.environ, {"HIFLY_BRAND": "windyfly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)


# ═══════════════════════════════════════════════════════════════════════
# Hatching Ceremony — Non-Negotiable
# ═══════════════════════════════════════════════════════════════════════


class TestHatchingCeremony:
    def test_hatching_always_true_windy(self):
        """HAS_HATCHING_CEREMONY must ALWAYS be True for Windy Fly."""
        with patch.dict(os.environ, {"HIFLY_BRAND": "windyfly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.HAS_HATCHING_CEREMONY is True

    def test_hatching_always_true_hifly(self):
        """HAS_HATCHING_CEREMONY must ALWAYS be True for HiFly."""
        with patch.dict(os.environ, {"HIFLY_BRAND": "hifly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.HAS_HATCHING_CEREMONY is True
            # Restore
        with patch.dict(os.environ, {"HIFLY_BRAND": "windyfly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)


# ═══════════════════════════════════════════════════════════════════════
# Ecosystem
# ═══════════════════════════════════════════════════════════════════════


class TestEcosystem:
    def test_ecosystem_true_for_windy(self):
        """HAS_ECOSYSTEM should be True for Windy Fly."""
        with patch.dict(os.environ, {"HIFLY_BRAND": "windyfly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.HAS_ECOSYSTEM is True

    def test_ecosystem_false_for_hifly(self):
        """HAS_ECOSYSTEM should be False for HiFly."""
        with patch.dict(os.environ, {"HIFLY_BRAND": "hifly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.HAS_ECOSYSTEM is False
            # Restore
        with patch.dict(os.environ, {"HIFLY_BRAND": "windyfly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)


# ═══════════════════════════════════════════════════════════════════════
# Constant Completeness
# ═══════════════════════════════════════════════════════════════════════


class TestBrandConstants:
    def test_all_constants_non_empty_windy(self):
        """All BRAND_* constants should have non-empty values for Windy Fly."""
        with patch.dict(os.environ, {"HIFLY_BRAND": "windyfly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.BRAND_NAME != ""
            assert b.BRAND_TAGLINE != ""
            assert b.BRAND_CLI != ""
            assert b.BRAND_URL != ""
            assert b.BRAND_EMOJI != ""

    def test_all_constants_non_empty_hifly(self):
        """All BRAND_* constants should have non-empty values for HiFly."""
        with patch.dict(os.environ, {"HIFLY_BRAND": "hifly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
            assert b.BRAND_NAME != ""
            assert b.BRAND_TAGLINE != ""
            assert b.BRAND_CLI != ""
            assert b.BRAND_URL != ""
            assert b.BRAND_EMOJI != ""
            # Restore
        with patch.dict(os.environ, {"HIFLY_BRAND": "windyfly"}):
            import importlib
            import windyfly.branding as b
            importlib.reload(b)
