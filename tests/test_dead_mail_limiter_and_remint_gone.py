"""Dead code removed (Hub 10-12, R4a).

- The local MailRateLimiter never ran: every Windy Mail adapter is built without a database, and
  the limiter only ran when it had one. Windy Mail enforces the limits itself and its 429 ("Daily
  send limit reached") reaches the owner word for word (tests/test_mail_one_message.py).
- service_auth.call_with_remint was never called: Windy Calendar re-mints on its own reasons
  (tests/test_windy_calendar.py pins them; Windy Calendar confirmed 10-12 that they are complete).
- cloud_provision.allocate_cloud_quota was never called: Windy Cloud gives an identity with no plan
  its free default, and an agent's writes count against its owner's plan (Windy Cloud, 10-12).
"""

from __future__ import annotations

import importlib.util

from windyfly.agent import service_auth
from windyfly.channels import email


def test_the_local_mail_limiter_is_gone():
    assert importlib.util.find_spec("windyfly.mail_rate_limiter") is None
    assert not hasattr(email, "RateLimitedError")


def test_the_unused_remint_helper_is_gone():
    assert not hasattr(service_auth, "call_with_remint")


def test_the_never_called_cloud_quota_request_is_gone():
    assert importlib.util.find_spec("windyfly.cloud_provision") is None
