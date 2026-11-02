"""An install with no owner configured must not trust anyone's real account.

Through 0.7.0, main.py defaulted AGENT_OWNER_TELEGRAM_ID to a maintainer's
personal Telegram ID, so every fresh install treated that account as owner.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "src" / "windyfly" / "main.py"


def test_no_numeric_telegram_id_literal_as_owner():
    # Guard the class, not the one value: no 8+ digit literal passed as the
    # telegram allowlist in main.py.
    src = MAIN.read_text()
    assert not re.search(r'allowed_user_ids=\[\s*"\d{6,}"', src)
