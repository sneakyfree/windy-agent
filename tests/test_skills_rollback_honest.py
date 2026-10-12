"""skills.rollback says it rolled back only when it did (P5.102.4; Hub 10-12).

The bridge answered {"rolled_back": true} for every call, including a skill with no earlier version
and an id that does not exist, while rollback_skill had changed nothing. The dashboard would show a
rollback that never happened. Now the answer is true only when a parent version was restored, and
otherwise says plainly why nothing changed.
"""

from __future__ import annotations

from tests.test_gap_closure import _make_bridge, _run
from windyfly.memory.skills import save_skill


def _promoted(db, skill_id: str) -> bool:
    return bool(db.fetchone("SELECT promoted FROM skills WHERE id = ?", (skill_id,))["promoted"])


def test_a_skill_with_no_earlier_version_is_not_rolled_back():
    bridge, db, _ = _make_bridge()
    try:
        sid = save_skill(db, "only", "Do it.", "playbook")
        db.execute("UPDATE skills SET promoted = TRUE WHERE id = ?", (sid,))
        db.commit()
        out = _run(bridge._dispatch("skills.rollback", {"skill_id": sid}))
        still = _promoted(db, sid)
    finally:
        db.close()
    assert out["rolled_back"] is False and out["skill_id"] == sid
    assert "no earlier version" in out["reason"]
    assert still is True                                   # nothing changed


def test_an_unknown_skill_is_not_rolled_back():
    bridge, db, _ = _make_bridge()
    try:
        out = _run(bridge._dispatch("skills.rollback", {"skill_id": "no-such-skill"}))
    finally:
        db.close()
    assert out["rolled_back"] is False and "no skill" in out["reason"].lower()


def test_a_skill_with_a_parent_is_rolled_back_to_it():
    bridge, db, _ = _make_bridge()
    try:
        parent = save_skill(db, "v1", "Do it.", "playbook")
        child = save_skill(db, "v2", "Do it better.", "playbook")
        db.execute("UPDATE skills SET parent_skill_id = ?, promoted = TRUE WHERE id = ?", (parent, child))
        db.commit()
        out = _run(bridge._dispatch("skills.rollback", {"skill_id": child}))
        child_on, parent_on = _promoted(db, child), _promoted(db, parent)
    finally:
        db.close()
    assert out == {"rolled_back": True, "skill_id": child, "restored_skill_id": parent}
    assert (child_on, parent_on) == (False, True)
