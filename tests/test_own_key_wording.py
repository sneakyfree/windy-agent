"""Hub's pinned wording (10-06): a refused OWN key is named plainly; every other Mind failure is
"I could not reach my AI just now."; the word "Mind" never reaches the owner; the git identity is the
agent's real mailbox address."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from windyfly.agent import loop, models


def _resp(status=502, error="connection", provider=None, body=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {k: v for k, v in {"x-mind-error": error, "x-mind-provider": provider}.items() if v}
    r.json.return_value = body if body is not None else {}
    r.text = json.dumps(body or {})
    return r


class TestRefusedKeyDetection:
    def test_x_mind_connection_header_names_the_provider(self):
        r = _resp()
        r.headers = {"x-mind-error": "connection", "x-mind-connection": "groq"}
        assert models._refused_key_provider(r) == "Groq"

    def test_x_mind_connection_alone_is_enough(self):
        r = _resp()
        r.headers = {"x-mind-connection": "anthropic"}
        assert models._refused_key_provider(r) == "Anthropic"

    def test_x_mind_connection_on_a_non_502_is_not_a_refusal(self):
        r = _resp(status=200)
        r.headers = {"x-mind-connection": "groq"}
        assert models._refused_key_provider(r) is None

    def test_header_names_the_provider(self):
        assert models._refused_key_provider(_resp(provider="groq")) == "Groq"

    def test_body_provider_field(self):
        assert models._refused_key_provider(_resp(body={"detail": {"provider": "anthropic", "message": "x"}})) == "Anthropic"

    def test_message_text_names_it(self):
        r = _resp(body={"detail": "your connected groq key was refused upstream - check the key/billing"})
        assert models._refused_key_provider(r) == "Groq"

    def test_no_provider_named(self):
        assert models._refused_key_provider(_resp(body={"detail": "refused"})) == ""

    def test_only_a_502_connection_error(self):
        assert models._refused_key_provider(_resp(status=503)) is None
        assert models._refused_key_provider(_resp(error="model_disabled")) is None
        assert models._refused_key_provider(_resp(status=403, error="connection")) is None


class TestBroker:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test")
        monkeypatch.setattr(models, "_provider_cooldowns", {})
        monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)

    def test_a_refused_key_is_recorded_without_cooling_mind_down(self, monkeypatch):
        monkeypatch.setattr("httpx.post", lambda *a, **k: _resp(provider="groq"))
        out = models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
        assert out is None and models._last_mind_failure == "mind key refused:Groq"
        assert not models._is_provider_in_cooldown("windy-mind")

    def test_other_5xx_still_cools_down(self, monkeypatch):
        monkeypatch.setattr("httpx.post", lambda *a, **k: _resp(status=503, error="upstream"))
        models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None)
        assert models._last_mind_failure == "mind http 503"


class TestBanner:
    def test_refused_key_pinned_sentence(self):
        b = loop._auto_resurrect_banner("llama3.2:3b", "mind key refused:Groq")
        assert "Your Groq key was refused. Check it in My AI." in b and "Mind" not in b

    def test_the_provider_is_read_out_of_the_chain_exhausted_summary(self):
        b = loop._auto_resurrect_banner("m", "All providers failed (anthropic:no-key, mind key refused:Groq, other)")
        assert "Your Groq key was refused." in b

    def test_refused_key_without_a_name(self):
        assert "Your key was refused. Check it in My AI." in loop._auto_resurrect_banner("m", "mind key refused:")

    @pytest.mark.parametrize("err", ["mind http 503", "mind http 429", "mind cooling down after errors", "mind unreachable (ConnectError)"])
    def test_every_other_mind_failure_is_plain_and_never_says_mind(self, err):
        b = loop._auto_resurrect_banner("llama3.2:3b", err)
        assert "I could not reach my AI just now. I will try again." in b
        assert "Mind" not in b and "key was refused" not in b and "dead" not in b

    def test_non_mind_failures_keep_their_wording(self):
        assert "hit a rate limit" in loop._auto_resurrect_banner("m", "429 too many")
        assert "rejected its credential" in loop._auto_resurrect_banner("m", "401 unauthorized")
        assert "Type /normal" in loop._auto_resurrect_banner("m", "x")


class TestGitIdentity:
    def test_uses_the_real_mailbox_address(self, monkeypatch):
        from windyfly.tools import outbound_identity as oi

        monkeypatch.setenv("WINDYMAIL_EMAIL", "name@windyfly.ai")
        assert oi.git_identity()[1] == "name@windyfly.ai"

    def test_no_mailbox_means_a_noreply_not_a_fake_inbox(self, monkeypatch):
        from windyfly.tools import outbound_identity as oi

        monkeypatch.delenv("WINDYMAIL_EMAIL", raising=False)
        assert oi.git_identity()[1] == "noreply@windyfly.ai"

    def test_the_windy_code_commands_carry_it_quoted(self, monkeypatch):
        from windyfly.tools import windycode

        monkeypatch.setenv("WINDYMAIL_EMAIL", "a b@windyfly.ai")
        args = windycode._git_identity_args()
        assert "user.email='a b@windyfly.ai'" in args and "agent@windymail.ai" not in args
