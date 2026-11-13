"""Contract tests for the trust check + gate.

Validates against the shape in /Users/thewindstorm/eternitas/docs/trust-api.md:
- GET /api/v1/trust/{passport} (note /api/v1/, not /v1/)
- Response fields: status, band, clearance_level, tier_multiplier,
  integrity_score, dimensions, allowed_actions, denied_actions,
  evaluated_at, cache_ttl_seconds
- 5-minute SQLite cache hit/miss and expiry (TTL honoured from body)
- Action mapping: agent gate vocabulary → Eternitas vocabulary
- Humans/no-passport → gate skipped
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from windyfly.memory.database import Database
from windyfly.trust import TrustDenied, TrustSnapshot, check_trust, require_trust
from windyfly.trust.check import (
    MAX_VERDICT_AGE_SECONDS,
    NO_FRESH_VERDICT,
    NO_LIVE_VERDICT,
    _cache_write,
    get_trust,
    map_gate_action,
)
from windyfly.trust.gate import require_trust_sync

ETERNITAS_BASE = "https://eternitas.windy.test"
PASSPORT = "ET26-K7BF-42MN"


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "agent.db")
    return Database(path)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ETERNITAS_URL", ETERNITAS_BASE)
    monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    monkeypatch.delenv("ETERNITAS_USE_MOCK", raising=False)
    monkeypatch.delenv("WINDYFLY_TRUST_STRICT", raising=False)
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "agent.db"))


def _live_response_body(**overrides) -> dict:
    """A canonical live Eternitas response body — override per-test."""
    body = {
        "passport_number": PASSPORT,
        "status": "active",
        "integrity_score": 842,
        "dimensions": {
            "honesty": 900, "reliability": 850, "compliance": 800,
            "safety": 780, "reputation": 820,
        },
        "band": "good",
        "clearance_level": "cleared",
        "tier_multiplier": 1.5,
        "allowed_actions": ["read", "send", "execute", "dm_bots", "install_packages"],
        "denied_actions": ["commit_push", "broadcast", "mention_strangers", "bypass_rate_caps"],
        "cache_ttl_seconds": 300,
        "evaluated_at": "2026-04-16T20:11:03.118+00:00",
    }
    body.update(overrides)
    return body


class TestActionMapping:
    def test_gate_vocabulary_maps_to_eternitas(self):
        assert map_gate_action("send_email") == "send"
        assert map_gate_action("post_chat_message") == "send"
        assert map_gate_action("run_command") == "execute"
        assert map_gate_action("install_package") == "install_packages"
        assert map_gate_action("commit_push") == "commit_push"
        assert map_gate_action("upload_file") == "send"

    def test_unknown_action_passes_through(self):
        assert map_gate_action("some_future_action") == "some_future_action"


class TestTrustFetchContract:
    @respx.mock
    async def test_correct_path_and_parse(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        snap = await get_trust(PASSPORT, db=db)

        assert snap is not None
        assert snap.status == "active"
        assert snap.band == "good"
        assert snap.clearance_level == "cleared"
        assert snap.tier_multiplier == 1.5
        assert snap.integrity_score == 842
        assert "send" in snap.allowed_actions
        assert "commit_push" in snap.denied_actions
        assert snap.dimensions["honesty"] == 900

    @respx.mock
    async def test_no_auth_header_sent(self, db):
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        await get_trust(PASSPORT, db=db)

        assert "Authorization" not in route.calls.last.request.headers

    @respx.mock
    async def test_caches_snapshot(self, db):
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        await get_trust(PASSPORT, db=db)
        await get_trust(PASSPORT, db=db)
        await get_trust(PASSPORT, db=db)

        assert route.call_count == 1

    @respx.mock
    async def test_cache_ttl_from_body_honoured(self, db):
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(cache_ttl_seconds=60))
        )

        await get_trust(PASSPORT, db=db)
        # Fast-forward the cached_at by 90 seconds, past the 60s TTL.
        db.conn.execute(
            "UPDATE trust_cache SET cached_at = ? WHERE passport = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(), PASSPORT),
        )
        db.conn.commit()

        await get_trust(PASSPORT, db=db)
        assert route.call_count == 2

    @respx.mock
    async def test_404_returns_none(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        assert await get_trust(PASSPORT, db=db) is None

    @respx.mock
    async def test_429_returns_none_without_crashing(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "60"})
        )
        assert await get_trust(PASSPORT, db=db) is None

    @respx.mock
    async def test_400_unrecognised_prefix_returns_none(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/XYZ-garbage").mock(
            return_value=httpx.Response(400, json={"error": "bad prefix"})
        )
        assert await get_trust("XYZ-garbage", db=db) is None

    @respx.mock
    async def test_mock_flag_skips_live_fetch(self, monkeypatch, db):
        monkeypatch.setenv("ETERNITAS_USE_MOCK", "true")
        route = respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body())
        )

        assert await get_trust(PASSPORT, db=db) is None
        assert not route.called


class TestGate:
    @respx.mock
    async def test_exceptional_bot_allowed_everything_in_vocabulary(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                band="exceptional",
                clearance_level="eternal",
                tier_multiplier=5.0,
                allowed_actions=[
                    "read", "send", "execute", "dm_bots", "install_packages",
                    "commit_push", "broadcast", "mention_strangers", "bypass_rate_caps",
                ],
                denied_actions=[],
            ))
        )
        for gate_action in ("send_email", "post_chat_message", "run_command",
                            "install_package", "commit_push", "upload_file"):
            decision = await require_trust(gate_action, db=db)
            assert decision.allowed, f"{gate_action} should be allowed"

    @respx.mock
    async def test_critical_band_blocks_everything(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                band="critical", tier_multiplier=0.0, allowed_actions=[],
            ))
        )
        for gate_action in ("send_email", "run_command", "commit_push", "upload_file"):
            with pytest.raises(TrustDenied):
                await require_trust(gate_action, db=db)

    @respx.mock
    async def test_suspended_status_blocks_everything(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                status="suspended",
                allowed_actions=["read", "send"],  # should be ignored
            ))
        )
        with pytest.raises(TrustDenied):
            await require_trust("send_email", db=db)

    @respx.mock
    async def test_revoked_status_blocks_everything(self, db):
        respx.get(f"{ETERNITAS_BASE}/api/v1/trust/{PASSPORT}").mock(
            return_value=httpx.Response(200, json=_live_response_body(
                status="revoked", allowed_actions=[],
            ))
        )
        with pytest.raises(TrustDenied):
            await require_trust("send_email", db=db)

    async def test_human_without_passport_skips_gate(self, db, monkeypatch):
        """No passport → gate is skipped entirely. No HTTP attempted."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        with respx.mock(assert_all_called=False) as mock:
            catch_all = mock.get(url__regex=rf"{ETERNITAS_BASE}/api/v1/trust/.*")

            decision = await require_trust("send_email", db=db, passport="")

        assert decision.allowed
        assert "no passport" in decision.reason
        assert not catch_all.called

    async def test_sync_gate_uses_cache(self, db):
        _cache_write(
            TrustSnapshot(
                passport=PASSPORT, status="active", band="good",
                clearance_level="cleared", tier_multiplier=1.5,
                integrity_score=800,
                allowed_actions=["read", "send"],
            ),
            db=db,
        )

        decision = require_trust_sync("send_email", db=db)
        assert decision.allowed

        with pytest.raises(TrustDenied):
            require_trust_sync("run_command", db=db)

    async def test_sync_gate_in_event_loop_fetches_instead_of_allowing(self, db):
        """Until 2026-11 a sync call from inside a running loop with no fresh cache was simply allowed, without
        asking Eternitas at all. It now fetches (on a helper thread) and the verdict decides."""
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            route = mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body()))
            assert require_trust_sync("send_email", db=db).allowed          # allowed BY THE VERDICT
            with pytest.raises(TrustDenied) as denied:
                require_trust_sync("commit_push", db=db)                    # the verdict denies it
            assert route.call_count == 1 and denied.value.unverified is False

    async def test_sync_gate_in_event_loop_with_no_answer_refuses(self, db):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(side_effect=httpx.ConnectError("eternitas unreachable"))
            with pytest.raises(TrustDenied) as denied:
                require_trust_sync("run_command", db=db)
        assert denied.value.unverified is True and denied.value.reason == NO_FRESH_VERDICT

    async def test_strict_mode_fails_closed_without_snapshot(self, db, monkeypatch):
        monkeypatch.delenv("ETERNITAS_URL", raising=False)
        monkeypatch.delenv("ETERNITAS_API_URL", raising=False)
        monkeypatch.setenv("WINDYFLY_TRUST_STRICT", "1")
        decision = await check_trust("send_email", db=db)
        assert not decision.allowed
        assert "strict" in decision.reason


class TestRevocationSurvivesOutage:
    """A revocation we have already seen must not be forgotten.

    The snapshot ages out after cache_ttl_seconds. If the refetch then
    fails, the default fail-open path would hand a revoked agent every
    action back — revocation defeated by pulling the network cable.
    """

    async def _seed_then_expire(self, db, status: str, seconds: int = 3600, **body):
        """Cache a real verdict, then age the row past its TTL."""
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body(status=status, **body)),
            )
            await check_trust("send_email", db=db)

        stale = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        db.conn.execute(
            "UPDATE trust_cache SET cached_at = ? WHERE passport = ?",
            (stale.isoformat(), PASSPORT),
        )
        db.conn.commit()

    @pytest.mark.parametrize("status", ["revoked", "suspended"])
    async def test_known_revocation_denies_when_service_unreachable(self, db, status):
        await self._seed_then_expire(db, status)

        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                side_effect=httpx.ConnectError("eternitas unreachable"),
            )
            decision = await check_trust("send_email", db=db)

        assert decision.allowed is False
        assert decision.snapshot.status == status
        assert "not forgotten" in decision.reason

    async def test_known_revocation_denies_without_strict_mode(self, db, monkeypatch):
        """Strict mode is opt-in; this denial must not depend on it."""
        monkeypatch.delenv("WINDYFLY_TRUST_STRICT", raising=False)
        await self._seed_then_expire(db, "revoked")

        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(503),
            )
            decision = await check_trust("run_command", db=db)

        assert decision.allowed is False

    async def test_reinstatement_requires_a_successful_fetch(self, db):
        """A live 'active' response clears the sticky denial."""
        await self._seed_then_expire(db, "revoked")

        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body(status="active")),
            )
            decision = await check_trust("send_email", db=db)

        assert decision.allowed is True


def _unreachable(mock):
    return mock.get(f"/api/v1/trust/{PASSPORT}").mock(side_effect=httpx.ConnectError("eternitas unreachable"))


class TestAHomeThatPullsItsTrust:
    """Eternitas's rule for every verifier, applied to the home (BOARD 2026-11-11T05:15:50Z): cache at most 5
    minutes; no refresh for 10 minutes = the passport counts as not good; a high-value act asks live."""

    _seed_then_expire = TestRevocationSurvivesOutage._seed_then_expire

    async def test_between_5_and_10_minutes_the_last_fetched_verdict_still_decides(self, db):
        await self._seed_then_expire(db, "active", seconds=400)
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            _unreachable(mock)
            yes = await check_trust("send_email", db=db)
            no = await check_trust("commit_push", db=db)             # NOT "unknown = everything"
        assert yes.allowed is True and "last verdict" in yes.reason and yes.unverified is False
        assert no.allowed is False and no.unverified is False and "commit_push" in no.reason

    async def test_after_10_minutes_without_a_refresh_every_gated_action_is_refused(self, db):
        await self._seed_then_expire(db, "active", seconds=MAX_VERDICT_AGE_SECONDS + 5)
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            _unreachable(mock)
            decisions = [await check_trust(a, db=db) for a in
                         ("send_email", "post_chat_message", "run_command", "install_package", "upload_file")]
        assert all(d.allowed is False and d.unverified is True and d.reason == NO_FRESH_VERDICT for d in decisions)

    async def test_a_passport_never_seen_is_refused_when_eternitas_does_not_answer(self, db):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            _unreachable(mock)
            decision = await check_trust("send_email", db=db)
        assert decision.allowed is False and decision.unverified is True

    @pytest.mark.parametrize("status_code", [404, 400, 429, 500, 503])
    async def test_an_answer_that_is_not_a_verdict_is_no_verdict(self, db, status_code):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(return_value=httpx.Response(status_code, json={}))
            decision = await check_trust("send_email", db=db)
        assert decision.allowed is False and decision.unverified is True

    async def test_the_first_successful_fetch_gives_everything_back(self, db):
        await self._seed_then_expire(db, "active", seconds=3600)
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            _unreachable(mock)
            assert (await check_trust("send_email", db=db)).allowed is False
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(return_value=httpx.Response(200, json=_live_response_body()))
            assert (await check_trust("send_email", db=db)).allowed is True

    async def test_a_person_with_no_passport_is_never_held_by_this(self, db, monkeypatch):
        monkeypatch.delenv("ETERNITAS_PASSPORT")
        with respx.mock(base_url=ETERNITAS_BASE, assert_all_called=False) as mock:
            route = _unreachable(mock)
            assert (await check_trust("send_email", db=db)).allowed is True
            assert not route.called

    async def test_the_mock_switch_still_asks_no_registry(self, db, monkeypatch):
        monkeypatch.setenv("ETERNITAS_USE_MOCK", "true")
        decision = await check_trust("send_email", db=db)
        assert decision.allowed is True and "ETERNITAS_USE_MOCK" in decision.reason

    # ── a high-value act asks live ──

    async def test_a_live_check_does_not_use_a_fresh_cache(self, db):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            route = mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body()))
            await check_trust("run_command", db=db)                  # fills the cache
            await check_trust("run_command", db=db)                  # served from it
            assert route.call_count == 1
            assert (await check_trust("run_command", db=db, live=True)).allowed is True
            assert route.call_count == 2

    async def test_a_live_check_sees_a_revocation_the_cache_has_not_heard_of(self, db):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(return_value=httpx.Response(200, json=_live_response_body()))
            assert (await check_trust("run_command", db=db)).allowed is True
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body(status="revoked", allowed_actions=[])))
            assert (await check_trust("run_command", db=db)).allowed is True              # the 5-minute cache
            assert (await check_trust("run_command", db=db, live=True)).allowed is False  # the live answer

    async def test_a_live_check_with_no_answer_refuses_even_with_a_fresh_cache(self, db):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(return_value=httpx.Response(200, json=_live_response_body()))
            await check_trust("run_command", db=db)
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            _unreachable(mock)
            decision = await check_trust("run_command", db=db, live=True)
        assert decision.allowed is False and decision.unverified is True and decision.reason == NO_LIVE_VERDICT

    async def test_the_sync_gate_asks_live_too(self, db):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            route = mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body()))
            require_trust_sync("run_command", db=db)
            require_trust_sync("run_command", db=db)
            assert route.call_count == 1
            require_trust_sync("run_command", db=db, live=True)
            assert route.call_count == 2


# Windy Legal's words (BOARD 2026-11-11T05:54:01Z), typed out here on purpose: a change to either constant fails.
LEGAL_FRESH_SENTENCE = ("I didn't do this, because I couldn't check with Eternitas that I'm still allowed to. "
                        "Ask me again in a few minutes.")
LEGAL_LIVE_SENTENCE = ("This needs a live check with Eternitas that I'm still allowed to do it, and Eternitas didn't "
                       "answer. I didn't do it; ask me again in a few minutes.")


def test_the_two_owner_sentences_are_legals_word_for_word():
    assert NO_FRESH_VERDICT == LEGAL_FRESH_SENTENCE and NO_LIVE_VERDICT == LEGAL_LIVE_SENTENCE
    assert "holding" not in NO_FRESH_VERDICT + NO_LIVE_VERDICT       # the action is refused, never queued


def test_a_longer_cache_life_suggested_by_the_registry_is_capped_at_five_minutes(db):
    from windyfly.trust.check import _capped_ttl

    assert [_capped_ttl(v) for v in (60, 300, 301, 86400, None, 0, "x")] == [60, 300, 300, 300, 300, 300, 300]


class TestTheOwnerAtTheKeyboard:
    """An outage at Eternitas must not lock a person out of their own agent (principle 10). A verdict AGAINST the
    agent is refused at the keyboard like anywhere else."""

    @pytest.fixture
    def run_cmd(self):
        import asyncio

        from windyfly.commands.registry import Command, CommandRegistry

        reg = CommandRegistry()
        ran: list[str] = []

        async def handler(ctx):
            ran.append("ran")
            return "done"

        reg.register(Command(name="run", description="x", category="12_developer", handler=handler))
        reg.register(Command(name="factory-reset", description="x", category="12_developer", handler=handler))
        # a category a remote channel MAY use, so the trust gate is what decides (not the channel policy)
        reg.register(Command(name="send-mail", description="x", category="14_email", handler=handler, dangerous=True))

        def go(name: str, platform: str) -> tuple[str, list[str]]:
            ran.clear()
            return asyncio.run(reg.execute(name, {"platform": platform})), list(ran)

        return go

    def test_no_answer_at_the_keyboard_goes_on(self, run_cmd):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            _unreachable(mock)
            out, ran = run_cmd("run", "terminal")
        assert ran == ["ran"] and "Trust gate denied" not in out

    def test_no_answer_from_a_remote_channel_is_refused(self, run_cmd):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            route = _unreachable(mock)
            out, ran = run_cmd("send-mail", "telegram")
        assert route.called and ran == [] and out == f"Trust gate denied /send-mail: {NO_FRESH_VERDICT}"

    def test_strict_mode_refuses_at_the_keyboard_too(self, run_cmd, monkeypatch):
        monkeypatch.setenv("WINDYFLY_TRUST_STRICT", "1")
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            _unreachable(mock)
            out, ran = run_cmd("run", "terminal")
        assert ran == [] and "strict" in out

    def test_a_revoked_agent_is_refused_at_the_keyboard_too(self, run_cmd):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body(status="revoked", allowed_actions=[])))
            out, ran = run_cmd("run", "terminal")
        assert ran == [] and out.startswith("Trust gate denied /run")

    def test_the_high_value_command_with_no_live_answer_is_refused_at_the_keyboard(self, run_cmd):
        """The keyboard exception is for ordinary gated commands. factory-reset needs a live answer everywhere."""
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            route = _unreachable(mock)
            out, ran = run_cmd("factory-reset", "terminal")           # nothing was ever fetched: still the live sentence
            assert route.called and ran == [] and out == f"Trust gate denied /factory-reset: {NO_LIVE_VERDICT}"
            assert out.endswith(LEGAL_LIVE_SENTENCE)                  # Legal: pinned word for word in this test
            out, ran = run_cmd("factory-reset", "cli")
            assert ran == [] and out == f"Trust gate denied /factory-reset: {NO_LIVE_VERDICT}"
            out, ran = run_cmd("run", "terminal")                     # the ordinary one still goes on
            assert ran == ["ran"]

    def test_a_good_cached_verdict_does_not_stand_in_for_the_live_answer(self, run_cmd):
        with respx.mock(base_url=ETERNITAS_BASE, assert_all_called=False) as mock:
            route = mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body()))
            run_cmd("run", "terminal")                                # fills the cache with a good verdict
            route.mock(side_effect=httpx.ConnectError("down"))
            out, ran = run_cmd("factory-reset", "terminal")
        assert ran == [] and out == f"Trust gate denied /factory-reset: {NO_LIVE_VERDICT}"

    def test_a_gate_that_itself_breaks_is_the_nobody_could_tell_case(self, run_cmd, monkeypatch):
        """The catch-all under the gate used to let every gated command run (Eternitas's note on 6702ebab)."""
        from windyfly.trust import gate

        def boom(*a, **k):
            raise RuntimeError("cache file locked")

        monkeypatch.setattr(gate, "require_trust_sync", boom)
        out, ran = run_cmd("send-mail", "telegram")
        assert ran == [] and out == f"Trust gate denied /send-mail: {NO_FRESH_VERDICT}"
        out, ran = run_cmd("factory-reset", "terminal")               # high-value: refused at the keyboard too
        assert ran == [] and out == f"Trust gate denied /factory-reset: {NO_FRESH_VERDICT}"
        out, ran = run_cmd("run", "terminal")                         # ordinary, at the owner's own keyboard
        assert ran == ["ran"]

    def test_the_high_value_command_asks_live(self, run_cmd):
        with respx.mock(base_url=ETERNITAS_BASE) as mock:
            route = mock.get(f"/api/v1/trust/{PASSPORT}").mock(
                return_value=httpx.Response(200, json=_live_response_body()))
            run_cmd("run", "terminal")
            run_cmd("run", "terminal")
            assert route.call_count == 1                              # ordinary gated command: the cache
            run_cmd("factory-reset", "terminal")
            assert route.call_count == 2                              # the high-value one: live
