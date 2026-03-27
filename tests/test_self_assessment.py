"""Tests for self-assessment."""

from windyfly.agent.self_assessment import run_self_assessment
from windyfly.memory.database import Database


class TestSelfAssessment:
    def test_generates_report(self):
        db = Database(":memory:")
        report = run_self_assessment(db)
        assert "scores" in report
        assert "grade" in report
        assert "overall_score" in report
        assert len(report["scores"]) == 6
        db.close()

    def test_grade_scale(self):
        from windyfly.agent.self_assessment import _score_to_grade
        assert _score_to_grade(95) == "A+"
        assert _score_to_grade(85) == "A"
        assert _score_to_grade(75) == "B"
        assert _score_to_grade(65) == "C"
        assert _score_to_grade(55) == "D"
        assert _score_to_grade(40) == "F"

    def test_assessment_stored_as_node(self):
        db = Database(":memory:")
        run_self_assessment(db)
        from windyfly.memory.nodes import get_nodes_by_type
        assessments = get_nodes_by_type(db, "self_assessment", limit=5)
        assert len(assessments) >= 1
        db.close()
