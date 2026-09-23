"""Owner pairing (SSO #13) — the replacement for Trust-On-First-Use.

The dashboard (hub-JWT authenticated) asks the agent for a one-time code
over the UDS bridge; the owner sends ``/pair <code>`` on any platform and
that sender becomes the platform's owner. Plus: the hatch pins the
owner's Matrix ID, and trust webhooks fail closed in production.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest

from windyfly.agent.capabilities import Band
from windyfly.bridge.uds_server import UDSBridge
from windyfly.channels import identity, pairing
from windyfly.channels.base import handle_incoming
from windyfly.channels.matrix_bot import hatch_owner_from_state
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.trust.verify import verify_webhook

HUB_ID = "4606f073-0f0f-41a7-bc90-d42fbb941349"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(tmp_path / "owner-bindings.json"))
    monkeypatch.setenv("WINDY_OWNER_PAIRING_PATH", str(tmp_path / "owner-pairing.json"))
    for var in ("WINDY_OWNER_IDS", "AGENT_OWNER_TELEGRAM_ID", "WINDY_LEGACY_OWNER_MODE"):
        monkeypatch.delenv(var, raising=False)
    identity._reset_warnings_for_tests()
    pairing._reset_for_tests()
    yield
    pairing._reset_for_tests()


def _handle(text, sender, platform="discord"):
    return asyncio.run(handle_incoming(text, {
        "platform": platform, "channel_id": "c1", "sender_id": sender,
    }))


class TestCodes:
    def test_code_shape(self):
        out = pairing.create_code(HUB_ID)
        code = out["code"]
        assert len(code) == 9 and code[4] == "-"
        assert all(ch in pairing.ALPHABET for ch in code.replace("-", ""))
        assert "expires_at" in out

    def test_only_hash_is_persisted(self, tmp_path):
        code = pairing.create_code(HUB_ID)["code"]
        raw = (tmp_path / "owner-pairing.json").read_text()
        assert code not in raw and code.replace("-", "") not in raw
        digest = hashlib.sha256(code.replace("-", "").encode()).hexdigest()
        assert digest in raw

    def test_store_is_owner_only(self, tmp_path):
        pairing.create_code(HUB_ID)
        mode = (tmp_path / "owner-pairing.json").stat().st_mode & 0o777
        assert mode == 0o600

    def test_email_owner_identity_refused(self):
        # Hub tokens can carry unverified emails; the owner is the id.
        with pytest.raises(ValueError):
            pairing.create_code("grant@example.com")

    def test_blank_owner_identity_refused(self):
        with pytest.raises(ValueError):
            pairing.create_code("  ")

    def test_live_code_cap(self, tmp_path):
        for _ in range(pairing._MAX_LIVE_CODES + 3):
            pairing.create_code(HUB_ID)
        codes = json.loads((tmp_path / "owner-pairing.json").read_text())["codes"]
        assert len(codes) == pairing._MAX_LIVE_CODES

    def test_normalize(self):
        assert pairing.normalize_code("abcd-efgh") == "ABCDEFGH"
        assert pairing.normalize_code("ABCD EFGH") == "ABCDEFGH"
        assert pairing.normalize_code("ABCD-EFG0") is None  # 0 not in alphabet
        assert pairing.normalize_code("ABC") is None


class TestPairing:
    def test_happy_path_binds_owner(self):
        code = pairing.create_code(HUB_ID)["code"]
        assert identity.resolve_band("discord", "u-42") == Band.SANDBOX
        was_cmd, reply = _handle(f"/pair {code}", "u-42")
        assert was_cmd and reply.startswith("Paired")
        assert identity.resolve_band("discord", "u-42") == Band.OWNER
        assert identity.resolve_band("discord", "someone-else") == Band.SANDBOX

    def test_case_and_separator_insensitive(self):
        code = pairing.create_code(HUB_ID)["code"]
        was_cmd, reply = _handle(f"/PAIR {code.lower().replace('-', ' ')}", "u-1")
        assert reply.startswith("Paired")

    def test_matrix_bang_prefix(self):
        code = pairing.create_code(HUB_ID)["code"]
        _, reply = _handle(f"!pair {code}", "@grant:hs", platform="matrix")
        assert reply.startswith("Paired")
        assert identity.resolve_band("matrix", "@grant:hs") == Band.OWNER

    def test_single_use(self):
        code = pairing.create_code(HUB_ID)["code"]
        assert _handle(f"/pair {code}", "u-1")[1].startswith("Paired")
        _, reply = _handle(f"/pair {code}", "u-2")
        assert reply == pairing.GENERIC_FAILURE
        assert identity.resolve_band("discord", "u-2") == Band.SANDBOX

    def test_expired(self, monkeypatch):
        code = pairing.create_code(HUB_ID, ttl_seconds=60)["code"]
        real = time.time
        monkeypatch.setattr(pairing.time, "time", lambda: real() + 61)
        _, reply = _handle(f"/pair {code}", "u-1")
        assert reply == pairing.GENERIC_FAILURE
        assert identity.resolve_band("discord", "u-1") == Band.SANDBOX

    def test_wrong_code(self):
        pairing.create_code(HUB_ID)
        _, reply = _handle("/pair ABCD-EFGH", "u-1")
        assert reply == pairing.GENERIC_FAILURE

    def test_bare_pair_is_generic_failure(self):
        was_cmd, reply = _handle("/pair", "u-1")
        assert was_cmd and reply == pairing.GENERIC_FAILURE

    def test_rate_limit_blocks_even_the_right_code(self):
        code = pairing.create_code(HUB_ID)["code"]
        for _ in range(pairing.FAIL_LIMIT):
            _handle("/pair ZZZZ-ZZZZ", "brute")
        _, reply = _handle(f"/pair {code}", "brute")
        assert reply == pairing.GENERIC_FAILURE
        assert identity.resolve_band("discord", "brute") == Band.SANDBOX
        # The limit is per sender: the real owner can still pair.
        assert _handle(f"/pair {code}", "owner")[1].startswith("Paired")

    def test_no_sender_cannot_pair(self):
        code = pairing.create_code(HUB_ID)["code"]
        assert pairing.try_pair("discord", None, f"/pair {code}") == pairing.GENERIC_FAILURE

    def test_non_pair_text_untouched(self):
        assert pairing.try_pair("discord", "u", "hello") is None
        assert pairing.try_pair("discord", "u", "/pairing is fun") is None

    def test_code_never_logged(self, caplog):
        code = pairing.create_code(HUB_ID)["code"]
        with caplog.at_level("DEBUG"):
            _handle(f"/pair {code}", "u-1")
            _handle("/pair ABCD-EFGH", "u-2")
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert code not in text and code.replace("-", "") not in text
        assert "ABCD-EFGH" not in text

    def test_loggable_masks(self):
        assert pairing.loggable("/pair ABCD-EFGH") == "/pair <redacted>"
        assert pairing.loggable("hi") == "hi"


class TestBridgeMethod:
    def test_owner_pair_create(self):
        bridge = UDSBridge({}, Database(":memory:"), WriteQueue())
        out = asyncio.run(bridge._dispatch("owner.pair.create",
                                           {"owner_identity": HUB_ID, "ttl_seconds": 300}))
        assert len(out["code"]) == 9
        assert _handle(f"/pair {out['code']}", "u-9")[1].startswith("Paired")

    def test_owner_pair_create_requires_identity(self):
        bridge = UDSBridge({}, Database(":memory:"), WriteQueue())
        with pytest.raises(ValueError):
            asyncio.run(bridge._dispatch("owner.pair.create", {}))


class TestHatchOwnerPin:
    AGENT = "@agent_et26-abcd-efgh:hs"

    def _create(self, creator):
        return {"type": "m.room.create", "sender": creator, "state_key": "",
                "content": {"creator": creator}}

    def _member(self, who, membership, sender=None):
        return {"type": "m.room.member", "state_key": who, "sender": sender or who,
                "content": {"membership": membership}}

    def test_invited_owner(self):
        ev = [self._create(self.AGENT), self._member(self.AGENT, "join"),
              self._member("@grant:hs", "invite", sender=self.AGENT)]
        assert hatch_owner_from_state(ev, self.AGENT) == "@grant:hs"

    def test_joined_owner(self):
        ev = [self._create(self.AGENT), self._member(self.AGENT, "join"),
              self._member("@grant:hs", "join")]
        assert hatch_owner_from_state(ev, self.AGENT) == "@grant:hs"

    def test_room_not_created_by_agent(self):
        ev = [self._create("@grant:hs"), self._member(self.AGENT, "join"),
              self._member("@grant:hs", "join")]
        assert hatch_owner_from_state(ev, self.AGENT) is None

    def test_ambiguous_room(self):
        ev = [self._create(self.AGENT), self._member("@grant:hs", "join"),
              self._member("@stranger:hs", "join")]
        assert hatch_owner_from_state(ev, self.AGENT) is None

    def test_left_member_ignored(self):
        ev = [self._create(self.AGENT), self._member("@grant:hs", "invite", sender=self.AGENT),
              self._member("@gone:hs", "leave")]
        assert hatch_owner_from_state(ev, self.AGENT) == "@grant:hs"

    def test_pin_binds_only_when_unowned(self):
        from windyfly.channels.matrix_bot import WindyFlyMatrixBot

        class _Resp:
            events = [
                {"type": "m.room.create", "sender": self.AGENT, "content": {"creator": self.AGENT}},
                {"type": "m.room.member", "state_key": "@grant:hs", "sender": self.AGENT,
                 "content": {"membership": "invite"}},
            ]

        class _Client:
            async def room_get_state(self, room_id):
                return _Resp()

        bot = WindyFlyMatrixBot.__new__(WindyFlyMatrixBot)
        bot.client = _Client()
        bot.bot_user_id = self.AGENT
        bot.config = {}
        asyncio.run(bot._pin_hatch_owner("!room:hs"))
        assert identity.resolve_band("matrix", "@grant:hs") == Band.OWNER

        # A known owner is never replaced by the pin.
        identity.bind_owner("matrix", "@already:hs")
        asyncio.run(bot._pin_hatch_owner("!room:hs"))
        assert identity.owner_ids()["matrix"] == {"@already:hs"}


class TestWebhookStrictInProduction:
    def test_production_without_secret_rejects(self, monkeypatch):
        monkeypatch.setenv("WINDYFLY_ENV", "production")
        r = verify_webhook(b"{}", {}, hmac_secret="", eternitas_url="")
        assert not r.ok and "not configured" in r.reason

    def test_production_bad_signature_rejects(self, monkeypatch):
        monkeypatch.setenv("WINDYFLY_ENV", "production")
        r = verify_webhook(b"{}", {"X-Eternitas-Signature": "sha256=00"},
                           hmac_secret="s3cret", eternitas_url="")
        assert not r.ok

    def test_dev_still_fails_open(self, monkeypatch):
        monkeypatch.delenv("WINDYFLY_ENV", raising=False)
        monkeypatch.delenv("WINDYFLY_TRUST_STRICT", raising=False)
        r = verify_webhook(b"{}", {}, hmac_secret="", eternitas_url="")
        assert r.ok


class TestTelegramAllowlist:
    def test_paired_owner_passes_the_env_allowlist(self):
        from windyfly.channels.telegram_bot import TelegramChannel

        ch = TelegramChannel(allowed_user_ids=["111"])
        assert ch._sender_allowed("111")
        assert not ch._sender_allowed("222")
        # /pair binds 222 as the telegram owner → the adapter lets them in
        # without a restart or an env edit.
        code = pairing.create_code(HUB_ID)["code"]
        assert pairing.try_pair("telegram", "222", f"/pair {code}").startswith("Paired")
        assert ch._sender_allowed("222")
        assert not ch._sender_allowed("333")

    def test_no_allowlist_allows_everyone_through_to_band_gating(self):
        from windyfly.channels.telegram_bot import TelegramChannel

        assert TelegramChannel(allowed_user_ids=[])._sender_allowed("anyone")
