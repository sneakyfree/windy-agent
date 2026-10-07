"""`windy commands --json`: the chat command registry as data (Boss GO 10-07)."""

import json
import subprocess
import sys

from windyfly.commands.export import commands_json


def test_every_registered_command_is_exported_with_the_agreed_fields():
    rows = commands_json()
    assert len(rows) >= 100
    keys = {"name", "description", "usage", "category", "aliases", "dangerous", "remote_allowed", "ecosystem_only"}
    assert all(set(r) == keys for r in rows)
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names))
    assert {"status", "model", "new", "reset", "undo", "whoami", "help", "forget", "budget"} <= set(names)


def test_remote_allowed_matches_the_channel_policy_and_dangerous_ones_are_flagged():
    by = {r["name"]: r for r in commands_json()}
    assert by["status"]["remote_allowed"] is True
    assert by["help"]["remote_allowed"] is True
    assert sum(1 for r in by.values() if r["remote_allowed"] and r["dangerous"]) >= 1


def test_cli_prints_valid_json():
    out = subprocess.run([sys.executable, "-m", "windyfly", "commands", "--json"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert any(r["name"] == "status" for r in json.loads(out.stdout))
