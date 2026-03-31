"""Test that failure detector doesn't trigger on innocent messages.

R1.1: Validates negative lookahead patterns and min-length guard
to prevent false positives on agreement phrases.
"""

from windyfly.agent.failure_detector import detect_friction


class TestFrictionPrecision:
    """Tests for friction pattern precision — avoiding false positives."""

    def test_no_false_positive_on_agreement(self):
        """'No problem' should NOT trigger factual_error."""
        assert detect_friction("No problem, that works great!") is None

    def test_no_false_positive_on_actually_positive(self):
        """'Actually, that's exactly right' should NOT trigger."""
        assert detect_friction("Actually, that's exactly right!") is None

    def test_no_false_positive_on_short_no(self):
        """Short messages like 'No' should be filtered by min-length guard."""
        assert detect_friction("No") is None

    def test_no_false_positive_on_no_worries(self):
        """'No worries' should NOT trigger factual_error."""
        assert detect_friction("No worries at all, thanks for trying!") is None

    def test_no_false_positive_on_no_thanks(self):
        """'No thanks' should NOT trigger factual_error."""
        assert detect_friction("No thanks, I'm good with that answer.") is None

    def test_true_positive_on_correction(self):
        """'No, that's wrong' SHOULD trigger factual_error."""
        f = detect_friction("No, that's wrong. The answer is 42.")
        assert f is not None
        assert f["fault_type"] == "factual_error"

    def test_true_positive_on_retry(self):
        """'Try again' SHOULD trigger execution_failure."""
        f = detect_friction("Try again, that code doesn't compile.")
        assert f is not None
        assert f["fault_type"] == "execution_failure"

    def test_true_positive_on_clarification(self):
        """'What I meant was' SHOULD trigger ambiguity_mishandled."""
        f = detect_friction("What I meant was the other approach, not this one.")
        assert f is not None
        assert f["fault_type"] == "ambiguity_mishandled"

    def test_true_positive_on_preference_miss(self):
        """'I already told you' SHOULD trigger preference_miss."""
        f = detect_friction("I already told you I don't want that format.")
        assert f is not None
        assert f["fault_type"] == "preference_miss"

    def test_short_message_filtered(self):
        """Messages under 10 chars should never trigger."""
        assert detect_friction("Wrong!") is None
        assert detect_friction("Retry") is None
        assert detect_friction("No way") is None
