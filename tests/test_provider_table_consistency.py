"""One provider table to rule them all (Sprint 2, 2026-07-04 audit).

Three tables used to answer "which providers, which env var, which
default model" — quickstart's, setup_wizard's, and the gateway's
TypeScript table — and they disagreed (the wizards recommended
claude-3-5-sonnet-latest long after the catalog moved to the 4.x
line). Python tables now derive from ``windyfly.provider_defaults``;
these tests pin the derivations. The gateway's TypeScript table went with
its provider store in Wave 3 (2026-10): the gateway offers no models.
"""

from __future__ import annotations


from windyfly.provider_defaults import (
    PROVIDER_DEFAULTS,
    by_name,
    key_detection_order,
)



class TestPythonDerivations:
    def test_quickstart_tables_derive_from_defaults(self):
        from windyfly.quickstart import KEY_PATTERNS, PROVIDER_MENU

        assert len(PROVIDER_MENU) == len(PROVIDER_DEFAULTS)
        for pat in KEY_PATTERNS:
            canon = by_name(pat["provider"])
            assert canon is not None
            assert pat["env_var"] == canon["env_var"]
            assert pat["model"] == canon["default_model"]

    def test_wizard_tables_derive_from_defaults(self):
        from windyfly.setup_wizard import MODEL_OPTIONS, PROVIDERS

        assert len(PROVIDERS) == len(PROVIDER_DEFAULTS)
        for row in PROVIDERS:
            canon = by_name(row["name"])
            assert canon is not None
            assert row["key"] == canon["env_var"]
        option_ids = {m["id"] for m in MODEL_OPTIONS}
        for p in PROVIDER_DEFAULTS:
            if p["default_model"] in option_ids or p["budget_model"] in option_ids:
                continue
            raise AssertionError(
                f"{p['name']}'s models missing from wizard MODEL_OPTIONS"
            )

    def test_key_prefix_specificity_order(self):
        """sk-ant- must be checked before the sk- catch-all, or every
        Anthropic key gets misdetected as OpenAI."""
        prefixes = [p["key_prefix"] for p in key_detection_order()]
        for i, earlier in enumerate(prefixes):
            for later in prefixes[i + 1:]:
                assert not later.startswith(earlier), (
                    f"general prefix {earlier!r} appears before the "
                    f"more-specific {later!r} and would shadow it"
                )
        assert prefixes.index("sk-ant-") < prefixes.index("sk-")

    def test_no_stale_default_models(self):
        """Defaults that made the 2026-07-04 audit's stale list must
        never come back."""
        stale = {"claude-3-5-sonnet-latest", "claude-4-opus", "claude-3-5-haiku-latest"}
        for p in PROVIDER_DEFAULTS:
            assert p["default_model"] not in stale
            assert p["budget_model"] not in stale
