"""The owner is told once when Eternitas lowers the agent's standing (LEGAL DECISION 53; trust/notice.py).

Before this the only thing that told an owner was the trust.changed push, which never reached a home with no
inbound port. Now the pull notices the change. Words: Windy Legal's, word for word. When "lowered" is said:
Eternitas's rule on its published bands.
"""

from __future__ import annotations

import asyncio

import pytest

from windyfly.agent import maintenance, owner_mail
from windyfly.memory.database import Database
from windyfly.trust import check, notice
from windyfly.trust.check import TrustSnapshot

PASSPORT = "ET26-TEST-NTC1"

# Windy Legal, BOARD 2026-11-11T06:41:02Z, closed 07:03:12Z, word for word.
LEGAL_53_LOWERED = ("Eternitas has lowered your agent's standing from {old} to {new}. Some things it could do before "
                    "may now be refused.")
LEGAL_53_SUSPENDED = ("Eternitas has suspended your agent's passport. Until that is lifted, things that need it will "
                      "be refused.")
LEGAL_53_REVOKED = ("Eternitas has revoked your agent's passport. Things that need it will be refused. You can appeal "
                    "at eternitas.ai.")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    monkeypatch.setenv("ETERNITAS_URL", "https://eternitas.test")
    monkeypatch.delenv("ETERNITAS_USE_MOCK", raising=False)
    return Database(str(tmp_path / "agent.db"))


@pytest.fixture
def mails(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(owner_mail, "send_sync", lambda subject, body: sent.append((subject, body)) or True)
    return sent


@pytest.fixture
def inline(monkeypatch, db):
    """The helper thread's work, done at once on this thread, on the test's own database."""
    monkeypatch.setattr(notice, "deliver_in_background", lambda passport: notice.deliver_pending(passport, db))


def _fetches(monkeypatch, *states):
    """Each get_trust(force_refresh=True) returns the next (status, band)."""
    it = iter(states)

    async def fetch(passport):
        status, band = next(it)
        return TrustSnapshot(passport=passport, status=status, band=band, allowed_actions=["send"])

    monkeypatch.setattr(check, "_fetch", fetch)


def _pull(db):
    return asyncio.run(check.get_trust(passport=PASSPORT, db=db, force_refresh=True))


# ── the words and the rule ──────────────────────────────────────────────────────────────────────────────────

def test_the_three_sentences_are_legals_word_for_word():
    assert notice.LOWERED == LEGAL_53_LOWERED
    assert notice.SUSPENDED == LEGAL_53_SUSPENDED
    assert notice.REVOKED == LEGAL_53_REVOKED
    assert "see why" not in (notice.LOWERED + notice.SUSPENDED + notice.REVOKED)   # not true today (Eternitas)
    assert notice.text_for("lowered", "good", "fair") == LEGAL_53_LOWERED.format(old="good", new="fair")


@pytest.mark.parametrize("old,new,kind", [
    (("active", "good"), ("active", "fair"), "lowered"),
    (("active", "exceptional"), ("active", "good"), "lowered"),
    (("active", "fair"), ("active", "poor"), "lowered"),
    (("active", "unproven"), ("active", "poor"), "lowered"),
    (("active", "poor"), ("active", "critical"), "lowered"),
    (("active", "good"), ("active", "unproven"), "lowered"),       # unproven counts as fair, and fair is below good
    (("active", "fair"), ("active", "unproven"), None),            # never "lowered"
    (("active", "unproven"), ("active", "fair"), None),
    (("active", "fair"), ("active", "good"), None),                # an improvement is never mailed
    (("active", "critical"), ("active", "poor"), None),
    (("active", "good"), ("active", "good"), None),
    (("active", "unknown"), ("active", "poor"), None),             # no published name to say "from"
    (("active", "good"), ("active", "something-new"), None),
    (("active", "good"), ("suspended", "good"), "suspended"),
    (("suspended", "good"), ("suspended", "poor"), None),          # still suspended: said once
    (("active", "good"), ("revoked", "critical"), "revoked"),
    (("suspended", "good"), ("revoked", "good"), "revoked"),
    (("revoked", "good"), ("revoked", "good"), None),
    (("suspended", "good"), ("active", "fair"), None),             # lifted: not a mail, even at a lower band
    (("active", "good"), ("being_born", "good"), None),
])
def test_when_each_notice_is_due(old, new, kind):
    assert notice.notice_for(*old, *new) == kind


# ── once per change ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_first_state_ever_seen_is_recorded_and_tells_nobody(db):
    assert notice.observe(PASSPORT, "suspended", "poor", db) is None
    assert notice.has_pending(PASSPORT, db) is False


def test_a_verdict_cached_before_this_code_arrived_is_what_the_first_fetch_is_compared_with(db):
    assert notice.observe(PASSPORT, "active", "poor", db, before=("active", "good")) == "lowered"


def test_a_change_is_owned_by_one_caller_only(db):
    notice.observe(PASSPORT, "active", "good", db)
    assert notice.observe(PASSPORT, "active", "fair", db) == "lowered"
    assert notice.observe(PASSPORT, "active", "fair", db) is None        # the second process, the same fetch
    other = Database(db.path) if hasattr(db, "path") else db             # and through another connection
    assert notice.observe(PASSPORT, "active", "fair", other) is None


def test_the_pull_tells_the_owner_once_and_not_again(db, mails, inline, monkeypatch):
    _fetches(monkeypatch, ("active", "good"), ("active", "poor"), ("active", "poor"), ("active", "poor"))
    for _ in range(4):
        assert _pull(db) is not None
    assert mails == [(notice.SUBJECTS["lowered"], LEGAL_53_LOWERED.format(old="good", new="poor"))]


def test_each_further_fall_is_its_own_change(db, mails, inline, monkeypatch):
    _fetches(monkeypatch, ("active", "good"), ("active", "fair"), ("suspended", "fair"), ("revoked", "fair"))
    for _ in range(4):
        _pull(db)
    assert [body for _, body in mails] == [LEGAL_53_LOWERED.format(old="good", new="fair"), LEGAL_53_SUSPENDED,
                                           LEGAL_53_REVOKED]


def test_an_improvement_tells_nobody(db, mails, inline, monkeypatch):
    _fetches(monkeypatch, ("active", "fair"), ("active", "good"), ("active", "exceptional"))
    for _ in range(3):
        _pull(db)
    assert mails == []


# ── a mail that could not be sent ───────────────────────────────────────────────────────────────────────────

def test_a_failed_mail_stays_pending_and_is_tried_again_after_ten_minutes_not_before(db, monkeypatch):
    tries: list[str] = []
    ok = {"v": False}
    monkeypatch.setattr(owner_mail, "send_sync", lambda subject, body: tries.append(body) or ok["v"])
    notice.observe(PASSPORT, "active", "good", db)
    assert notice.observe(PASSPORT, "suspended", "good", db) == "suspended"
    assert notice.deliver_pending(PASSPORT, db, now=1000.0) is False
    assert notice.has_pending(PASSPORT, db) is True
    assert notice.deliver_pending(PASSPORT, db, now=1000.0 + notice.RETRY_AFTER_S - 1) is False
    assert len(tries) == 1                                               # not asked again inside ten minutes
    ok["v"] = True
    assert notice.deliver_pending(PASSPORT, db, now=1000.0 + notice.RETRY_AFTER_S + 1) is True
    assert tries == [LEGAL_53_SUSPENDED, LEGAL_53_SUSPENDED]
    assert notice.has_pending(PASSPORT, db) is False
    assert notice.deliver_pending(PASSPORT, db, now=9999.0) is False     # sent: never again


def test_a_pending_notice_goes_when_the_standing_recovers_first(db, monkeypatch):
    monkeypatch.setattr(owner_mail, "send_sync", lambda subject, body: False)
    notice.observe(PASSPORT, "active", "good", db)
    notice.observe(PASSPORT, "active", "fair", db)
    notice.deliver_pending(PASSPORT, db, now=1000.0)
    assert notice.observe(PASSPORT, "active", "good", db) is None        # back up before the owner was told
    assert notice.has_pending(PASSPORT, db) is False


def test_a_later_pull_retries_a_pending_notice(db, mails, monkeypatch):
    delivered: list[str] = []
    monkeypatch.setattr(notice, "deliver_in_background", lambda passport: delivered.append(passport))
    _fetches(monkeypatch, ("active", "good"), ("active", "poor"), ("active", "poor"))
    _pull(db)
    assert delivered == []
    _pull(db)
    _pull(db)                                                            # nothing changed, but one is pending
    assert delivered == [PASSPORT, PASSPORT]


# ── the check itself is never in the way ────────────────────────────────────────────────────────────────────

def test_a_broken_notice_never_breaks_the_verdict(db, monkeypatch):
    _fetches(monkeypatch, ("active", "good"))
    monkeypatch.setattr(notice, "observe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    snap = _pull(db)
    assert snap is not None and snap.band == "good"
    assert check._cache_read(PASSPORT, db=db).band == "good"


def test_the_mail_never_runs_on_the_callers_thread(db, monkeypatch):
    import threading

    seen: list[str] = []
    done = threading.Event()
    monkeypatch.setattr(notice, "deliver_pending", lambda passport, d: (seen.append(threading.current_thread().name),
                                                                        done.set()))
    notice.deliver_in_background(PASSPORT)
    assert done.wait(5) and seen == ["trust-owner-notice"]


# ── an idle agent still pulls ───────────────────────────────────────────────────────────────────────────────

def test_the_maintenance_list_pulls_the_standing_every_tick():
    jobs = {j.name: j for j in maintenance.default_jobs({"memory": {"db_path": ":memory:"}})}
    assert "eternitas.trust_pull" in jobs


def test_the_pull_job_asks_nothing_without_a_passport(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    asked: list[str] = []
    monkeypatch.setattr(check, "pull_soon", lambda passport: asked.append(passport))
    job = {j.name: j for j in maintenance.default_jobs({"memory": {"db_path": ":memory:"}})}["eternitas.trust_pull"]
    job.run()
    assert asked == []
    monkeypatch.setenv("ETERNITAS_PASSPORT", PASSPORT)
    job.run()
    assert asked == [PASSPORT]


def test_the_timed_pull_waits_its_own_offset_on_a_helper_thread_and_never_raises(monkeypatch):
    """Many agents on one home restart in the same second; each pulls at its own fixed offset (Eternitas's ask)."""
    import threading

    slept: list[float] = []
    asked: list[tuple[str, str]] = []

    async def get_trust(passport=None, **k):
        asked.append((passport, threading.current_thread().name))
        raise RuntimeError("registry down")

    monkeypatch.setattr(check.time, "sleep", slept.append)
    monkeypatch.setattr(check, "get_trust", get_trust)
    t = check.pull_soon(PASSPORT)
    t.join(5)
    assert not t.is_alive()
    assert slept == [check.pull_offset(PASSPORT)] and 0 <= slept[0] < check.PULL_SPREAD_S
    assert asked == [(PASSPORT, "trust-pull")]


def test_offsets_are_fixed_per_passport_and_spread_out():
    offsets = {check.pull_offset(f"ET26-TEST-{i:04d}") for i in range(200)}
    assert check.pull_offset(PASSPORT) == check.pull_offset(PASSPORT)
    assert len(offsets) > 60 and max(offsets) < check.PULL_SPREAD_S


# ── Boss's nit on #781: one sentence for "no live answer" ───────────────────────────────────────────────────

@pytest.mark.parametrize("cached", [False, True])
def test_a_live_check_that_times_out_on_the_helper_thread_says_the_live_sentence(db, monkeypatch, cached):
    from windyfly.trust import gate

    if cached:   # a fresh cached yes must not stand in for the live answer either
        check._cache_write(TrustSnapshot(passport=PASSPORT, status="active", band="good", allowed_actions=["*"]),
                           db=db)

    async def never_in_time(*a, **k):
        await asyncio.sleep(1.0)

    monkeypatch.setattr(gate, "require_trust", never_in_time)
    monkeypatch.setattr(check, "_TIMEOUT", -1.95)          # the helper thread gets 0.05 s

    async def inside_a_loop():
        with pytest.raises(gate.TrustDenied) as e:
            gate.require_trust_sync("run_command", db=db, live=True)
        return e.value

    denied = asyncio.run(inside_a_loop())
    assert denied.reason == check.NO_LIVE_VERDICT and denied.unverified is True
