"""The first text to a contact says exactly what the shared table records (LEGAL DECISION 119 Q3 (B)).

windy-contracts schema/agent/tool-phrases.v1.json records sms.first_text_intro, and since 1.4.0 it ends with
Windy's one help address. The table only RECORDS the sentence; the words this body sends are tools/sms.py's
_FIRST_TEXT. This test holds the two together: vendored and hash-pinned (see the .SOURCE file), the say compared
byte for byte, and the table's windy-fly source pointing at this file. Windy Text refuses a first text without the
address only after these words are live on Windy 0 (Boss RULING 2026-11-12T06:48:57Z)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from windyfly.tools import sms

DIR = Path(__file__).parent / "contracts" / "agent"
TABLE = json.loads((DIR / "tool-phrases.v1.json").read_text())
ENTRY = TABLE["x-phrases"]["sms.first_text_intro"]


def test_the_vendored_table_is_the_pinned_one():
    src = (DIR / "tool-phrases.v1.SOURCE").read_text().split()
    assert TABLE["x-version"] == "1.4.0"
    assert hashlib.sha256((DIR / "tool-phrases.v1.json").read_bytes()).hexdigest() == src[src.index("sha256") + 1]


def test_the_first_text_is_the_tables_words_byte_for_byte():
    assert sms._FIRST_TEXT == ENTRY["say"]


def test_the_help_address_is_fixed_not_a_slot():
    assert ENTRY["say"].endswith("Reply STOP to opt out, HELP for help, or email support@windyword.ai.")
    assert set(re.findall(r"\{(\w+)\}", ENTRY["say"])) == set(ENTRY["slots"]) == {"agent", "owner", "message"}


def test_the_tables_source_names_this_file():
    assert ENTRY["runtimes"]["windy-fly"].startswith("src/windyfly/tools/sms.py:")
    line = int(ENTRY["runtimes"]["windy-fly"].split(":")[1].split()[0])
    src_lines = Path(sms.__file__).read_text().splitlines()
    assert src_lines[line - 1].startswith("_FIRST_TEXT = ")
