"""Skill engine v1 (Sprint 3): files + progressive disclosure + skill.*.

Pins the loop the 2026-07-04 audit found missing end-to-end:
learn (skill.save) → persist (DB + SKILL.md mirror) → survive restart
(file sync) → recall (prompt index + skill.view).
"""

from __future__ import annotations

import pytest

from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.skill_learning import (
    register_skill_learning_capabilities,
)
from windyfly.memory.database import Database
from windyfly.skills.files import (
    parse_skill_file,
    render_skill_file,
    sanitize_skill_name,
    skills_dir,
    sync_skill_files,
)

PLAYBOOK = (
    "1. cd to the site directory\n"
    "2. run `wrangler deploy`\n"
    "3. purge the Cloudflare cache for the zone\n"
)


@pytest.fixture()
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture()
def reg(db):
    r = CapabilityRegistry()
    register_skill_learning_capabilities(r, db)
    return r


def _save(reg, **kw):
    args = {
        "name": "deploy-website",
        "description": "Deploy the site end to end",
        "body": PLAYBOOK,
    }
    args.update(kw)
    return reg.get("skill.save").handler(**args)


class TestFileFormat:
    def test_round_trip(self):
        text = render_skill_file(
            name="deploy-website", description="Deploy it",
            body=PLAYBOOK, tags="deploy",
        )
        parsed = parse_skill_file(text)
        assert parsed["name"] == "deploy-website"
        assert parsed["description"] == "Deploy it"
        assert "wrangler deploy" in parsed["body"]

    def test_malformed_returns_none(self):
        assert parse_skill_file("no frontmatter here") is None
        assert parse_skill_file("---\nname: x\n---\n") is None  # empty body

    def test_name_sanitization(self):
        assert sanitize_skill_name("Deploy My Website!") == "deploy-my-website"
        assert sanitize_skill_name("../../etc/passwd") is None or "/" not in (
            sanitize_skill_name("../../etc/passwd") or ""
        )
        assert sanitize_skill_name("") is None


class TestSaveViewList:
    def test_save_promotes_and_mirrors_to_file(self, reg, db):
        result = _save(reg)
        assert result["ok"], result
        assert result["version"] == 1
        path = skills_dir() / "deploy-website.md"
        assert path.exists()
        assert "wrangler deploy" in path.read_text(encoding="utf-8")

    def test_view_returns_body_and_counts_usage(self, reg, db):
        _save(reg)
        view = reg.get("skill.view").handler(name="deploy-website")
        assert view["ok"]
        assert "wrangler deploy" in view["body"]
        again = reg.get("skill.view").handler(name="deploy-website")
        assert again["ok"]
        from windyfly.memory.skills import get_skill_by_name
        assert (get_skill_by_name(db, "deploy-website")["usage_count"] or 0) >= 1

    def test_list_shows_index_not_bodies(self, reg, db):
        _save(reg)
        idx = reg.get("skill.list").handler()
        assert idx["count"] == 1
        entry = idx["skills"][0]
        assert entry["name"] == "deploy-website"
        assert "wrangler" not in str(entry)  # index stays compact

    def test_resave_bumps_version_with_lineage(self, reg, db):
        _save(reg)
        result = _save(reg, body=PLAYBOOK + "4. verify DNS\n")
        assert result["ok"] and result["version"] == 2
        view = reg.get("skill.view").handler(name="deploy-website")
        assert "verify DNS" in view["body"]

    def test_save_rejects_garbage(self, reg):
        assert not _save(reg, name="!!!")["ok"]
        assert not _save(reg, body="hi")["ok"]
        assert not _save(reg, body="x" * 9000)["ok"]

    def test_view_unknown_skill(self, reg):
        assert not reg.get("skill.view").handler(name="nope")["ok"]


class TestFileSync:
    def test_dropped_file_becomes_promoted_skill(self, db):
        d = skills_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "backup-photos.md").write_text(render_skill_file(
            name="backup-photos", description="Back up photos",
            body="1. rsync to the NAS\n2. verify checksums\n",
        ))
        stats = sync_skill_files(db)
        assert stats["ingested"] == 1
        from windyfly.memory.skills import get_skill_by_name
        skill = get_skill_by_name(db, "backup-photos")
        assert skill and skill["promoted"]
        assert skill["language"] == "playbook"

    def test_sync_is_idempotent_and_versions_changes(self, db):
        d = skills_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / "backup-photos.md"
        f.write_text(render_skill_file(
            name="backup-photos", description="Back up photos",
            body="1. rsync to the NAS\n",
        ))
        assert sync_skill_files(db)["ingested"] == 1
        assert sync_skill_files(db)["unchanged"] == 1
        f.write_text(render_skill_file(
            name="backup-photos", description="Back up photos",
            body="1. rsync to the NAS\n2. also to R2\n",
        ))
        assert sync_skill_files(db)["updated"] == 1

    def test_malformed_file_is_skipped_not_fatal(self, db):
        d = skills_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "broken.md").write_text("not a skill file")
        stats = sync_skill_files(db)
        assert stats["malformed"] == 1


class TestPromptIndex:
    def test_prompt_carries_compact_skill_index(self, reg, db):
        from windyfly.agent.prompt import assemble_prompt

        _save(reg)
        messages = assemble_prompt(
            config={}, db=db, user_message="deploy the site please",
            session_id="t:1:v1",
        )
        joined = "\n".join(
            m["content"] for m in messages if m["role"] == "system"
        )
        assert "Skills you know" in joined
        assert "deploy-website" in joined
        assert "wrangler" not in joined  # bodies stay out of context


class TestSkillVerdict:
    """Strand P3.31.10: a viewed skill is judged on the owner's next message."""

    def test_friction_counts_a_failure_and_a_calm_reply_a_success(self, reg, db):
        from windyfly.agent.capabilities.audit import set_current_session_id
        from windyfly.memory.skills import get_skill_by_name
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.skills.manager import judge_skill_uses

        _save(reg)
        wq = WriteQueue()
        wq.start()
        try:
            set_current_session_id("s-verdict")
            reg.get("skill.view").handler(name="deploy-website")
            assert judge_skill_uses(db, wq, "s-verdict", friction=True) == 1
            reg.get("skill.view").handler(name="deploy-website")
            assert judge_skill_uses(db, wq, "s-verdict", friction=False) == 1
            assert judge_skill_uses(db, wq, "s-verdict", friction=False) == 0      # nothing left to judge
        finally:
            set_current_session_id(None)
            wq.stop()
        row = get_skill_by_name(db, "deploy-website")
        assert (row["failure_count"], row["success_count"]) == (1, 1)
        assert (row["usage_count"] or 0) == 2                                     # the views, counted once each

    def test_the_turn_after_a_skill_was_used_judges_it(self, reg, db):
        """End to end: the agent views a skill, the owner answers with a correction, the skill gets a failure."""
        from unittest.mock import patch

        from tests.test_strand_wave4_agent_loop import _llm_result, _make_config
        from windyfly.agent import loop
        from windyfly.agent.capabilities.audit import set_current_session_id
        from windyfly.memory.episodes import save_episode
        from windyfly.memory.skills import get_skill_by_name
        from windyfly.memory.write_queue import WriteQueue

        _save(reg)
        save_episode(db, "user", "how do I deploy?", session_id="s-e2e")
        save_episode(db, "assistant", "Run wrangler deploy from the repo root.", session_id="s-e2e")
        set_current_session_id("s-e2e")
        reg.get("skill.view").handler(name="deploy-website")
        set_current_session_id(None)
        wq = WriteQueue()
        wq.start()
        try:
            with patch.object(loop, "call_llm", return_value=_llm_result("Sorry, let me fix that.")), \
                 patch.object(loop, "is_online", return_value=True):
                loop.agent_respond(_make_config(), db, wq, "No, that's wrong, it failed", "s-e2e")
        finally:
            wq.stop()
        assert get_skill_by_name(db, "deploy-website")["failure_count"] == 1
