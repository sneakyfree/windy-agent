"""send_email names only what every client has: a text reply (Grant's Build-131 note 7).

The agent told Grant to "tap send on the email draft card", and the phone had no such card. No client tells
this agent that it shows cards or buttons, so the tool description and the draft's note speak only of
replying 'send' or 'cancel'.
"""

from __future__ import annotations

import re

import pytest

from windyfly.tools import mail

_UI_WORDS = re.compile(r"\b(card|button|tap)\b", re.IGNORECASE)


@pytest.mark.parametrize("confirm", ["1", "0", ""])
def test_description_never_names_a_card_or_button(monkeypatch, confirm):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", confirm)
    text = mail._send_email_description()
    assert not _UI_WORDS.search(text.replace("Never mention a card or a button to tap.", "")), text


def test_held_draft_description_says_reply_send_or_cancel(monkeypatch):
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    text = mail._send_email_description()
    assert "replying 'send'" in text and "'cancel'" in text
    assert "Never mention a card or a button to tap." in text
    assert "do not ask for a yes in text first" in text  # still one confirmation, not two (Build 116)
