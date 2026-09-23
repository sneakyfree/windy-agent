"""An install with no owner configured must not trust anyone's real account.

Through 0.7.0, main.py defaulted AGENT_OWNER_TELEGRAM_ID to a maintainer's
personal Telegram ID, so every fresh install treated that account as owner.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "src" / "windyfly" / "main.py"


def test_agent_owner_telegram_id_has_no_default_account():
    src = MAIN.read_text()
    m = re.search(r'os\.environ\.get\(\s*"AGENT_OWNER_TELEGRAM_ID"\s*,\s*"([^"]*)"', src)
    assert m is not None, "owner lookup moved; update this guard"
    assert m.group(1) == "", "AGENT_OWNER_TELEGRAM_ID must not default to a real account"


def test_unset_owner_means_no_allowlist():
    assert "allowed_user_ids=[owner_id] if owner_id else []" in MAIN.read_text()


def test_no_numeric_telegram_id_literal_as_owner():
    # Guard the class, not the one value: no 8+ digit literal passed as the
    # telegram allowlist in main.py.
    src = MAIN.read_text()
    assert not re.search(r'allowed_user_ids=\[\s*"\d{6,}"', src)
