"""Mail's refusals reach the owner as Mail's own sentence in every shape Mail sends, never raw JSON
(Windy Mail 10-12, main a9b22a9; Hub R5).

/send has no custom exception handler, so FastAPI wraps most refusals as {"detail": <value>}:
every 429 (daily and per-minute, sender and owner scope) carries a DICT detail with a "message";
the 400 tier limit, 422 invalid address and the 403s carry a STRING detail. Only the send-from
refusals (from_not_owned 422, from_unsupported_mode) are top-level {"error", "message"}. The dict
case used to reach the owner as the raw JSON string.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from windyfly.channels.email import _mail_error_sentence


def _resp(status: int, body) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


@pytest.mark.parametrize("status,body,expect", [
    (429, {"detail": {"sent": False, "error": "sender_daily_limit",
                      "message": "NOT sent: daily send limit reached (5/5).", "retry_after_s": 3600,
                      "scope": "sender"}}, "NOT sent: daily send limit reached (5/5)."),
    (429, {"detail": {"sent": False, "error": "sender_rate_limited",
                      "message": "NOT sent: too many sends this minute.", "retry_after_s": 60,
                      "scope": "owner"}}, "NOT sent: too many sends this minute."),
    (400, {"detail": "Too many recipients (12); tier limit is 10"}, "Too many recipients (12); tier limit is 10"),
    (422, {"detail": "Invalid recipient address: nope"}, "Invalid recipient address: nope"),
    (403, {"detail": "Account suspended due to low sender reputation."}, "Account suspended due to low sender reputation."),
    (422, {"error": "from_not_owned", "message": "You can only send from your own addresses."},
     "You can only send from your own addresses."),
])
def test_every_shape_mail_sends_reaches_the_owner_as_mails_sentence(status, body, expect):
    assert _mail_error_sentence(_resp(status, body)) == expect


def test_a_dict_detail_without_a_message_says_its_code_never_json():
    out = _mail_error_sentence(_resp(429, {"detail": {"sent": False, "error": "sender_daily_limit"}}))
    assert out == "sender_daily_limit" and "{" not in out


def test_an_unreadable_refusal_is_a_plain_sentence_never_json():
    out = _mail_error_sentence(_resp(418, {"detail": {"sent": False}}))
    assert out == "The mail server refused it." and "{" not in out
