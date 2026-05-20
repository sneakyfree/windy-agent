"""Provider cooldown layer respects ``is_permanent_auth_error``
classifier — PR #209 follow-up.

The cooldown escalator (30s exponential) is designed for TRANSIENT
failures: a provider 503s a few times, we back off, it recovers.
For PERMANENT failures (401 invalid x-api-key from a dead/expired
token), the same escalator means the provider gets retried after
30s, 60s, 90s — wasted calls that will all 401 again. PR #209's
``is_permanent_auth_error`` classifier already short-circuits
auto_resurrect on perma-auth; this PR plumbs the same signal into
the chain-cooldown layer so a dead provider stays out of rotation
for an hour (_COOLDOWN_AUTH_DEAD_S) rather than 30s.
"""

from __future__ import annotations

import time

from windyfly.agent.models import (
    _COOLDOWN_AUTH_DEAD_S,
    _COOLDOWN_BASE_S,
    _is_provider_in_cooldown,
    _record_provider_failure,
    _record_provider_success,
)


def setup_function(fn):
    """Clear cooldowns between tests so they don't bleed."""
    from windyfly.agent import models
    models._provider_cooldowns.clear()


def test_transient_failure_uses_short_cooldown():
    """A single transient failure (no perma-auth markers) should
    get the standard 30s base cooldown."""
    _record_provider_failure(
        "anthropic", error_str="Error code: 503 - service unavailable",
    )
    from windyfly.agent import models
    until, count = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert _COOLDOWN_BASE_S * 0.9 <= delta <= _COOLDOWN_BASE_S * 1.1
    assert count == 1


def test_permanent_auth_failure_uses_long_cooldown():
    """A 401-invalid-x-api-key failure should set a 1-hour
    cooldown so the chain skips the doomed provider."""
    err = ("Error code: 401 - {'type': 'error', 'error': "
           "{'type': 'authentication_error', 'message': "
           "'invalid x-api-key'}}")
    _record_provider_failure("anthropic", error_str=err)
    from windyfly.agent import models
    until, _ = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert delta >= _COOLDOWN_AUTH_DEAD_S * 0.95
    assert delta <= _COOLDOWN_AUTH_DEAD_S * 1.05


def test_403_permission_uses_long_cooldown():
    err = "403 - {'type': 'permission_error', 'message': 'org disabled'}"
    _record_provider_failure("anthropic", error_str=err)
    from windyfly.agent import models
    until, _ = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert delta >= _COOLDOWN_AUTH_DEAD_S * 0.95


def test_ambiguous_401_uses_short_cooldown():
    """A 401 without an auth marker (intermediary error) is
    treated as transient — safer to retry than to lock out an
    hour on a maybe-recoverable error."""
    _record_provider_failure(
        "anthropic", error_str="401 from proxy",
    )
    from windyfly.agent import models
    until, _ = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert delta <= _COOLDOWN_BASE_S * 1.1


def test_429_rate_limit_uses_short_cooldown():
    """Rate limits ARE transient (5-hour rolling window resets).
    Don't lock out an hour on a 429."""
    _record_provider_failure(
        "anthropic", error_str="Error code: 429 - rate_limit_exceeded",
    )
    from windyfly.agent import models
    until, _ = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert delta <= _COOLDOWN_BASE_S * 1.1


def test_no_error_str_still_works_back_compat():
    """Pre-PR callers don't pass error_str — should still get the
    standard transient cooldown."""
    _record_provider_failure("anthropic")
    from windyfly.agent import models
    until, _ = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert delta <= _COOLDOWN_BASE_S * 1.1


def test_success_clears_cooldown():
    _record_provider_failure(
        "anthropic", error_str="503 transient",
    )
    assert _is_provider_in_cooldown("anthropic") is True
    _record_provider_success("anthropic")
    assert _is_provider_in_cooldown("anthropic") is False


def test_transient_cooldown_escalates_on_repeat():
    """Multiple transient failures escalate exponentially. The
    AUTH_DEAD path bypasses this escalator — the failure-count is
    still bumped but the cooldown is always 1h regardless."""
    for _ in range(3):
        _record_provider_failure("anthropic", error_str="503 transient")
    from windyfly.agent import models
    until, count = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert count == 3
    # 30 * 3 = 90s, well below the auth-dead bucket
    assert delta < _COOLDOWN_AUTH_DEAD_S / 2


def test_perma_auth_does_not_escalate_with_count():
    """Even on the 4th perma-auth failure, cooldown stays at
    _COOLDOWN_AUTH_DEAD_S — escalator doesn't apply to the
    perma-auth path."""
    err = ("401 - {'type': 'authentication_error', 'message': "
           "'invalid x-api-key'}")
    for _ in range(4):
        _record_provider_failure("anthropic", error_str=err)
    from windyfly.agent import models
    until, count = models._provider_cooldowns["anthropic"]
    delta = until - time.time()
    assert count == 4
    # Should still be ~1h, not 4*1h
    assert delta < _COOLDOWN_AUTH_DEAD_S * 1.05
