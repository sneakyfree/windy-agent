"""Every workflow that runs on a PR runs on EVERY PR: no paths / paths-ignore filter on its triggers (Boss 2026-10-28:
windy-pro's docs-only PRs skipped their gates through paths-ignore). A path filter coming back fails this test."""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _trigger_block(text: str) -> str:
    """The workflow's `on:` block: from the top-level `on:` key to the next top-level key (usually `jobs:`)."""
    m = re.search(r"(?m)^(on|\"on\"|'on'):\s*$(.*?)(?=^\S)", text, re.S)
    return m.group(2) if m else ""


def test_there_are_workflows_to_check():
    assert len(list(WORKFLOWS.glob("*.yml"))) >= 3


def test_no_workflow_trigger_has_a_path_filter():
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        block = _trigger_block(wf.read_text(encoding="utf-8"))
        if re.search(r"(?m)^\s+(paths|paths-ignore)\s*:", block):
            offenders.append(wf.name)
    assert offenders == [], f"path filters on workflow triggers (they let PRs skip gates): {offenders}"


def test_the_check_sees_a_path_filter_when_there_is_one():
    sample = "name: x\non:\n  pull_request:\n    paths-ignore:\n      - 'docs/**'\njobs:\n  a: {}\n"
    assert re.search(r"(?m)^\s+(paths|paths-ignore)\s*:", _trigger_block(sample))
