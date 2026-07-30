"""The test suite must never read the machine's real credentials.

Two paths did, and both were found the same way: three tests failed on the
OC5 iMac and nowhere else, which looked like an Intel-specific bug for two
campaigns. It wasn't. Those were the only boxes where the contaminating
files happened to exist.

1. ``providers._overrides_path()`` defaulted to the **cwd-relative**
   ``data/providers.json``, resolved once at import. OC5 had one holding a
   live dashboard-saved OpenAI key, so a test asserting "an unconfigured
   provider is skipped" watched openai get called.
2. ``models._reload_oauth_token()`` read ``~/.claude/.credentials.json``
   and **wrote the token into os.environ** — process-global, surviving
   every later test. The fleet credential cron puts a real token there on
   every machine, so one auth-failure path armed the whole run with a live
   billable key.

Both are now env-overridable and pinned to tmp by ``conftest``. These tests
guard the override plumbing itself; if someone re-hardcodes either path,
they fail here instead of on one unlucky machine months later.
"""

from __future__ import annotations

import json
from pathlib import Path

from windyfly.agent import models
from windyfly.agent import providers


class TestProviderOverridesPath:
    def test_explicit_env_var_wins(self, tmp_path, monkeypatch):
        target = tmp_path / "somewhere" / "providers.json"
        monkeypatch.setenv("WINDYFLY_PROVIDERS_PATH", str(target))
        path, is_explicit = providers._overrides_path()
        assert path == target
        assert is_explicit is True

    def test_default_is_absolute_not_cwd_relative(self, tmp_path, monkeypatch):
        """A relative default meant the agent's provider config depended on
        the directory it was launched from — grandma's saved providers
        vanishing based on cwd."""
        monkeypatch.delenv("WINDYFLY_PROVIDERS_PATH", raising=False)
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
        path, is_explicit = providers._overrides_path()
        assert path.is_absolute(), f"provider overrides path must be absolute: {path}"
        assert is_explicit is False

    def test_explicit_path_ignores_legacy_file(self, tmp_path, monkeypatch):
        """The bug that survived the first fix attempt.

        When the path is pinned, the cwd-relative legacy file must not be
        consulted *at all*. The first version migrated it into the pinned
        location — faithfully re-importing the contamination it existed to
        remove, leaving OC5's failures unchanged.
        """
        legacy = tmp_path / "data" / "providers.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"openai": {"api_key": "REAL-KEY"}}))
        monkeypatch.setattr(providers, "_LEGACY_OVERRIDES_PATH", legacy)
        monkeypatch.setenv(
            "WINDYFLY_PROVIDERS_PATH", str(tmp_path / "pinned.json"),
        )

        assert providers._load_overrides() == {}
        assert not (tmp_path / "pinned.json").exists(), \
            "legacy file was migrated into a pinned path — contamination"

    def test_legacy_file_still_migrates_when_not_pinned(self, tmp_path, monkeypatch):
        """A real install must not lose its configured providers to the
        path change (#8: stability over elegance)."""
        legacy = tmp_path / "data" / "providers.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"openai": {"api_key": "k"}}))
        monkeypatch.setattr(providers, "_LEGACY_OVERRIDES_PATH", legacy)
        monkeypatch.delenv("WINDYFLY_PROVIDERS_PATH", raising=False)
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))

        assert providers._load_overrides() == {"openai": {"api_key": "k"}}
        assert (tmp_path / "state" / "providers.json").exists(), \
            "existing user's providers were not migrated — config loss"


class TestOAuthTokenReload:
    def test_credentials_path_is_overridable(self, tmp_path, monkeypatch):
        creds = tmp_path / "creds.json"
        creds.write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "sk-ant-oat01-FROM-OVERRIDE"}},
        ))
        monkeypatch.setenv("WINDY_CLAUDE_CREDENTIALS_PATH", str(creds))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "stale")

        assert models._reload_oauth_token() is True
        import os
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-oat01-FROM-OVERRIDE"

    def test_missing_file_is_a_quiet_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "WINDY_CLAUDE_CREDENTIALS_PATH", str(tmp_path / "nope.json"),
        )
        assert models._reload_oauth_token() is False

    def test_conftest_points_it_away_from_the_real_home(self):
        """The guard that actually matters: under the suite's own fixtures,
        this must never resolve to the operator's real credentials."""
        import os
        configured = os.environ.get("WINDY_CLAUDE_CREDENTIALS_PATH")
        assert configured, \
            "conftest must pin WINDY_CLAUDE_CREDENTIALS_PATH"
        real = Path.home() / ".claude" / ".credentials.json"
        assert Path(configured) != real
        assert not Path(configured).exists()

    def test_conftest_points_provider_overrides_away_too(self):
        import os
        configured = os.environ.get("WINDYFLY_PROVIDERS_PATH")
        assert configured, "conftest must pin WINDYFLY_PROVIDERS_PATH"
        assert Path(configured) != Path("data/providers.json").resolve()
