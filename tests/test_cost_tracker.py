"""Tests for the cost tracker with budget enforcement."""

from __future__ import annotations

from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.cost_tracker import check_budget, get_monthly_spend
from windyfly.memory.database import Database


class TestCostTracker:
    def test_monthly_spend_empty(self):
        db = Database(":memory:")
        assert get_monthly_spend(db) == 0.0
        db.close()

    def test_monthly_spend_with_entries(self):
        db = Database(":memory:")
        log_cost(db, "gpt-4o-mini", 100, 50, 0.001)
        log_cost(db, "gpt-4o-mini", 200, 100, 0.002)
        assert abs(get_monthly_spend(db) - 0.003) < 0.0001
        db.close()


class TestBudgetCheck:
    def test_within_budget(self):
        db = Database(":memory:")
        config = {"costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0}}
        result = check_budget(db, config, proposed_cost=0.1)
        assert result["allowed"] is True
        assert result["warning"] is False
        db.close()

    def test_exceeds_budget(self):
        db = Database(":memory:")
        # Log enough to exceed a $0.50 budget
        for _ in range(100):
            log_cost(db, "gpt-4o", 1000, 500, 0.1)
        config = {"costs": {"daily_budget_usd": 0.50, "warn_at_usd": 0.30}}
        result = check_budget(db, config, proposed_cost=0.1)
        assert result["allowed"] is False
        db.close()

    def test_warning_threshold(self):
        db = Database(":memory:")
        log_cost(db, "gpt-4o", 1000, 500, 3.5)
        config = {"costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0}}
        result = check_budget(db, config, proposed_cost=0.0)
        assert result["warning"] is True
        assert result["allowed"] is True
        db.close()

    def test_default_config(self):
        db = Database(":memory:")
        result = check_budget(db, {}, proposed_cost=0.0)
        assert result["allowed"] is True
        assert result["daily_budget"] == 5.0
        db.close()
