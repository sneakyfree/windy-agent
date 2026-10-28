"""Every workflow that runs on a PR runs on EVERY PR: no paths / paths-ignore filter on its triggers (Boss 2026-10-28:
windy-pro's docs-only PRs skipped their gates through paths-ignore). The workflows are PARSED as YAML (Boss's ruling:
block and flow form alike), so `pull_request: {paths: [...]}` or `on: {pull_request: {paths: [...]}}` can't slip by."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
FILTER_KEYS = ("paths", "paths-ignore")


def _triggers(doc: dict) -> object:
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    return doc.get("on", doc.get(True))


def path_filtered_events(text: str) -> list[str]:
    """The trigger events of one workflow that carry a paths / paths-ignore filter."""
    doc = yaml.safe_load(text) or {}
    on = _triggers(doc)
    if not isinstance(on, dict):  # `on: push` or `on: [push, pull_request]` cannot carry a filter
        return []
    return sorted(event for event, cfg in on.items()
                  if isinstance(cfg, dict) and any(k in cfg for k in FILTER_KEYS))


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def test_there_are_workflows_to_check():
    assert len(_workflow_files()) >= 3


def test_no_workflow_trigger_has_a_path_filter():
    offenders = {wf.name: ev for wf in _workflow_files() if (ev := path_filtered_events(wf.read_text(encoding="utf-8")))}
    assert offenders == {}, f"path filters on workflow triggers (they let PRs skip gates): {offenders}"


@pytest.mark.parametrize("sample,events", [
    ("on:\n  pull_request:\n    paths-ignore:\n      - 'docs/**'\njobs: {}\n", ["pull_request"]),
    ("on:\n  pull_request: {paths: ['**/compose.yml']}\njobs: {}\n", ["pull_request"]),
    ("on: {pull_request: {paths: [a]}, push: {branches: [main]}}\njobs: {}\n", ["pull_request"]),
    ("'on':\n  push:\n    paths: [x]\njobs: {}\n", ["push"]),
    ("on:\n  pull_request_target:\n    paths-ignore: ['*.md']\njobs: {}\n", ["pull_request_target"]),
    ("on: [push, pull_request]\njobs: {}\n", []),
    ("on: push\njobs: {}\n", []),
    ("on:\n  pull_request:\n  push:\n    branches: [main]\njobs: {}\n", []),
])
def test_the_check_sees_block_and_flow_forms(sample, events):
    assert path_filtered_events(sample) == events
