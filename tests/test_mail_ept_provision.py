"""Mail tool disambiguation.

There is ONE send tool: the agent's own Windy Mail mailbox (Hub, 2026-10-02).
The Gmail/Resend ``email.send`` capability is gone, so no agent ever has a
second, owner-address send path. (The CLI-side mailbox provisioning these tests
used to cover was removed in 0.7.5 with the terminal hatch — ADR-059.)
"""

from __future__ import annotations

import pytest



@pytest.fixture(autouse=True)
def _clean_mail_env(monkeypatch, tmp_path):
    for var in (
        "ETERNITAS_PASSPORT_TOKEN", "WINDYMAIL_PROVISION_SERVICE_TOKEN",
        "WINDYMAIL_SERVICE_TOKEN", "WINDYMAIL_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # _write_env writes to cwd/.env


class TestNoSecondSendPath:
    def test_no_email_capability_in_the_boot_sequence(self):
        from windyfly.agent.boot import default_capability_registration_sequence

        names = [step.name for step in default_capability_registration_sequence()]
        assert "capabilities.email" not in names

    def test_gmail_token_file_does_not_bring_back_a_sender(self, tmp_path):
        # A Gmail token left on disk from an older install must not matter.
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "gmail_token.json").write_text("{}")
        import importlib.util

        assert importlib.util.find_spec("windyfly.agent.capabilities.email") is None
