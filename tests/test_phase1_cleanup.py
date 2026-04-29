"""Phase 1 cleanup regressions — _saved_sliders + _active_timers.

Both were module-level globals with the same shape of bug as #93/#94:
shared state polluting other "users" (sessions / restarts).

  - shape_shift._saved_sliders was a single LIST; concurrent
    shape_shift() calls from different sessions could pop each
    other's saved sliders.
  - utilities._active_timers used ``len()+1`` as ID generator, so
    after a timer expired/was-removed, the next set_timer would
    reuse the same ID — confusing referenced timers. Plus expired
    entries never got cleaned, growing the dict forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ── shape_shift._saved_sliders per-user ────────────────────────────


class TestSavedSlidersPerUser:
    def setup_method(self):
        from windyfly.agent.shape_shift import _saved_sliders
        _saved_sliders.clear()

    def teardown_method(self):
        from windyfly.agent.shape_shift import _saved_sliders
        _saved_sliders.clear()

    def test_initially_empty(self):
        from windyfly.agent.shape_shift import _saved_sliders
        assert _saved_sliders == {}

    def test_per_user_stack_shape(self):
        """Two users each push one saved snapshot — they must be on
        separate stacks. Pre-fix the global LIST would have appended
        both, and pop() in one user's restore would yank the other's."""
        from windyfly.agent.shape_shift import _saved_sliders
        _saved_sliders.setdefault("alice", []).append({"warmth": 9})
        _saved_sliders.setdefault("bob", []).append({"warmth": 3})
        assert _saved_sliders["alice"] == [{"warmth": 9}]
        assert _saved_sliders["bob"] == [{"warmth": 3}]
        # Bob's pop must NOT affect alice
        bob_state = _saved_sliders["bob"].pop()
        assert bob_state == {"warmth": 3}
        assert _saved_sliders["alice"] == [{"warmth": 9}]


# ── utilities._active_timers ID + cleanup ─────────────────────────


class TestActiveTimers:
    def setup_method(self):
        from windyfly.tools.utilities import _active_timers
        _active_timers.clear()

    def teardown_method(self):
        from windyfly.tools.utilities import _active_timers
        _active_timers.clear()

    def test_set_timer_returns_uuid_shaped_id(self):
        from windyfly.tools.utilities import set_timer
        result = set_timer("5 minutes")
        assert result["success"] is True
        # UUID-based: starts with "timer-" + 8 hex chars
        assert result["id"].startswith("timer-")
        assert len(result["id"]) == 6 + 8

    def test_two_timers_get_different_ids(self):
        from windyfly.tools.utilities import set_timer
        a = set_timer("5 minutes")
        b = set_timer("10 minutes")
        assert a["id"] != b["id"]

    def test_expired_timers_purged_on_next_set(self):
        """Pre-fix: expired entries piled up indefinitely, leaking
        memory in long-running processes. New: purge on each set."""
        from windyfly.tools.utilities import _active_timers, set_timer
        # Plant a timer manually that's already in the past.
        _active_timers["zombie"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert "zombie" in _active_timers
        set_timer("5 minutes")  # this triggers _purge_expired
        assert "zombie" not in _active_timers

    def test_id_no_collision_after_expiry(self):
        """Pre-fix: ID was f'timer-{len()+1}'. After 1 timer expired
        and was removed, the next set_timer would name the new one
        'timer-1' again — colliding with whatever the user had
        referenced. New: UUID prefix prevents reuse."""
        from windyfly.tools.utilities import _active_timers, set_timer
        a = set_timer("5 minutes")
        first_id = a["id"]
        # Simulate the first timer expiring and being purged
        _active_timers[first_id] = datetime.now(timezone.utc) - timedelta(seconds=1)
        b = set_timer("10 minutes")
        # b's ID must NOT be the same as a's
        assert b["id"] != first_id
