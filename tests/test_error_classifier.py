"""Tests for the cross-channel error classifier.

Covers the heuristic mapping from raw exceptions to user-facing
messages and verifies the WINDYFLY_ERROR_VERBOSE flag controls the
diagnostic suffix.
"""

from __future__ import annotations

import pytest

from windyfly.channels.errors import (
    ClassifiedError,
    ErrorCategory,
    classify,
)


@pytest.fixture(autouse=True)
def _verbose_default(monkeypatch):
    # Default to verbose so the diagnostic suffix is exercised.
    monkeypatch.setenv("WINDYFLY_ERROR_VERBOSE", "1")


def test_llm_failure_503():
    err = classify(RuntimeError("LLM call failed across all providers: 503"))
    assert err.category == ErrorCategory.LLM_FAILURE
    assert "AI service" in err.user_message
    assert "llm_failure" in err.user_message  # diagnostic suffix


def test_llm_failure_failover_chain_exhausted():
    # The error message that PR #46 raises when the whole chain is dead
    err = classify(RuntimeError(
        "LLM call failed across all providers in chain "
        "(attempted=['openai(gpt-4o-mini)']): 500"
    ))
    assert err.category == ErrorCategory.LLM_FAILURE


def test_rate_limit():
    err = classify(RuntimeError("429 Too Many Requests"))
    assert err.category == ErrorCategory.LLM_RATE_LIMIT
    assert "slow down" in err.user_message


def test_auth_401():
    err = classify(RuntimeError("401 invalid x-api-key"))
    assert err.category == ErrorCategory.LLM_AUTH
    assert "credentials" in err.user_message


def test_named_authentication_error():
    class AuthenticationError(Exception):
        pass

    err = classify(AuthenticationError("token expired"))
    assert err.category == ErrorCategory.LLM_AUTH


def test_timeout():
    class TimeoutError_(Exception):
        pass

    err = classify(TimeoutError_("request timed out"))
    assert err.category == ErrorCategory.LLM_TIMEOUT


def test_db_locked():
    err = classify(Exception("database is locked"))
    assert err.category == ErrorCategory.DB_FAILURE
    assert "memory" in err.user_message


def test_budget_exceeded():
    class BudgetExceeded(Exception):
        pass

    err = classify(BudgetExceeded("daily budget reached"))
    assert err.category == ErrorCategory.BUDGET_EXCEEDED
    assert "budget" in err.user_message.lower()


def test_network_connection_error():
    class ConnectionError_(Exception):
        pass

    err = classify(ConnectionError_("name resolution failed"))
    assert err.category == ErrorCategory.NETWORK


def test_unknown_falls_through():
    err = classify(ValueError("something weird"))
    assert err.category == ErrorCategory.UNKNOWN
    assert "Something went wrong" in err.user_message
    assert "ValueError" in err.user_message  # appears in diagnostic suffix


def test_log_message_always_includes_class_and_category():
    err = classify(RuntimeError("LLM call failed: 503"))
    assert "llm_failure" in err.log_message
    assert "RuntimeError" in err.log_message


def test_verbose_off_strips_diagnostic_suffix(monkeypatch):
    monkeypatch.setenv("WINDYFLY_ERROR_VERBOSE", "0")
    err = classify(RuntimeError("LLM call failed: 503"))
    assert err.category == ErrorCategory.LLM_FAILURE
    assert "AI service" in err.user_message
    assert "diagnostic:" not in err.user_message
    # log_message stays verbose regardless of band
    assert "RuntimeError" in err.log_message


def test_returns_dataclass_with_three_fields():
    err = classify(RuntimeError("anything"))
    assert isinstance(err, ClassifiedError)
    assert err.category in ErrorCategory
    assert isinstance(err.user_message, str)
    assert isinstance(err.log_message, str)
