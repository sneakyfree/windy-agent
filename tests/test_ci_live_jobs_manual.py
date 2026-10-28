"""LIVE-HOST JOBS are manual or off (Boss 2026-10-28 07:36Z): a workflow job that reaches a live host or a public
download (deploy, ssh/scp/rsync, S3, GitHub releases, PyPI, a container registry, the app stores) must be `if: false`
or sit in a workflow whose ONLY trigger is workflow_dispatch: never on a push, tag, schedule or PR.
Workflows are PARSED as YAML. A "sight" test proves the scan still finds this repo's known live jobs."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

LIVE_USES = re.compile(
    r"^(pypa/gh-action-pypi-publish|softprops/action-gh-release|ncipollo/release-action|docker/build-push-action|"
    r"cloudflare/wrangler-action|cloudflare/pages-action|appleboy/ssh-action|appleboy/scp-action|"
    r"burnett01/rsync-deployments|easingthemes/ssh-deploy|aws-actions/|r0adkll/upload-google-play|"
    r"apple-actions/upload-testflight-build|peaceiris/actions-gh-pages|JamesIves/github-pages-deploy-action|"
    r"actions/deploy-pages)", re.I)
LIVE_RUN = re.compile(
    r"(^|[\s;&|(])(ssh|scp|rsync|wrangler|twine\s+upload|uv\s+publish|fastlane)(?=\s|$)|pages\s+deploy|"
    r"compose\b[^\n]*\bup\b|"
    r"aws\s+s3\s+(cp|sync|mv|rm)\b|gh\s+release\s+(create|upload)\b|uploads\.github\.com|docker\s+push\b|"
    r"\bnpm\s+(run\s+(deploy|release|publish)|publish)\b|scripts/[\w.-]*(deploy|publish|release)[\w.-]*\.sh", re.I)


def _triggers(doc: dict) -> set[str]:
    on = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` as True
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(x) for x in on}
    if isinstance(on, dict):
        return {str(k) for k in on}
    return set()


def _is_off(job: dict) -> bool:
    cond = job.get("if")
    return cond is False or (isinstance(cond, str) and cond.strip().replace(" ", "") in ("false", "${{false}}"))


def _is_live(job: dict) -> bool:
    # A job that calls a reusable workflow can't be inspected here: treat it as live (the conservative side).
    if job.get("uses"):
        return True
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if LIVE_USES.search(str(step.get("uses") or "")) or LIVE_RUN.search(str(step.get("run") or "")):
            return True
    return False


def live_jobs(text: str) -> dict[str, bool]:
    """{job name: True when it is allowed (off, or the workflow is workflow_dispatch-only)} for every live job."""
    doc = yaml.safe_load(text) or {}
    manual_only = _triggers(doc) == {"workflow_dispatch"}
    return {name: manual_only or _is_off(job)
            for name, job in (doc.get("jobs") or {}).items() if isinstance(job, dict) and _is_live(job)}


def _workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def test_sight_the_scan_still_finds_this_repos_live_jobs():
    found = {f"{wf.name}:{job}" for wf in _workflows() for job in live_jobs(wf.read_text(encoding="utf-8"))}
    assert {"release.yml:publish-pypi", "release.yml:publish-image"} <= found, found


def test_every_live_job_is_manual_or_off():
    bad = [f"{wf.name}:{job}" for wf in _workflows()
           for job, ok in live_jobs(wf.read_text(encoding="utf-8")).items() if not ok]
    assert bad == [], f"live-host jobs that can run without a person: {bad}"


LIVE_STEP = "    steps:\n      - uses: pypa/gh-action-pypi-publish@release/v1\n"


@pytest.mark.parametrize("sample,expected", [
    ("on:\n  push:\n    tags: ['v*']\njobs:\n  p:\n" + LIVE_STEP, {"p": False}),
    ("on: [push, workflow_dispatch]\njobs:\n  p:\n" + LIVE_STEP, {"p": False}),
    ("on:\n  schedule: [{cron: '0 0 * * *'}]\njobs:\n  p:\n    steps:\n      - run: rsync -a dist/ host:/srv\n",
     {"p": False}),
    ("on: {pull_request: {}}\njobs:\n  d:\n    steps:\n      - run: npx wrangler pages deploy dist\n", {"d": False}),
    ("on:\n  push: {}\njobs:\n  s:\n    steps:\n      - run: aws s3 sync out s3://bucket\n", {"s": False}),
    ("on:\n  workflow_dispatch:\njobs:\n  p:\n" + LIVE_STEP, {"p": True}),
    ("on: workflow_dispatch\njobs:\n  p:\n" + LIVE_STEP, {"p": True}),
    ("on: push\njobs:\n  p:\n    if: false\n" + LIVE_STEP, {"p": True}),
    ("on: push\njobs:\n  t:\n    steps:\n      - run: uv run pytest\n      - run: echo ssh-free\n", {}),
    ("on: push\njobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n", {}),
    ("on: push\njobs:\n  g:\n    steps:\n      - uses: peaceiris/actions-gh-pages@v4\n", {"g": False}),
    ("on: push\njobs:\n  n:\n    steps:\n      - run: npm run deploy\n", {"n": False}),
    ("on: push\njobs:\n  s:\n    steps:\n      - run: ./scripts/deploy-prod.sh\n", {"s": False}),
    ("on: push\njobs:\n  r:\n    uses: ./.github/workflows/deploy.yml\n", {"r": False}),
    ("on: workflow_dispatch\njobs:\n  r:\n    uses: ./.github/workflows/deploy.yml\n", {"r": True}),
])
def test_the_scan_sees_live_jobs_and_their_triggers(sample, expected):
    assert live_jobs(sample) == expected
