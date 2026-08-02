# Branch triage, 2026-08-02 — 16 stale branches archived and deleted

Requested by `eternitas/docs/hatch-consolidation/05-windy-agent-CLEANUP.md`
TASK 4: *"15 unmerged remote branches, 0 open PRs … Every one is a path a
future instance can wander down."*

**Result: the remote went from 17 branches to `master` + one open PR.**

---

## Nothing was lost. Every branch is recoverable.

The brief said to delete and rely on the reflog. I did something stronger,
because that advice does not hold for a remote: I cannot read GitHub's reflog,
and unreferenced objects are eventually garbage-collected. A SHA written in a
document is only as good as the object still existing.

**So each branch tip was tagged and the tags were pushed and verified on the
remote BEFORE any deletion.** Tags keep the objects alive indefinitely and
cost nothing, and they do not clutter the branch list.

Recover any of them with:

```
git fetch origin --tags
git checkout -b <name> archive/2026-08-02/<name-with-dashes>
```

| Archive tag (all prefixed `archive/2026-08-02/`) | Tip |
|---|---|
| `gauntlet-phase-2-1-blind-excepts` | `99099bd` |
| `gauntlet-phase-2-2-1-fsm-doc` | `c85f259` |
| `gauntlet-phase-2-2-2-lifeboat-fsm-observability` | `f5afa80` |
| `gauntlet-phase-2-2-2-perma-auth-extract` | `56ee518` |
| `gauntlet-phase-2-2-b-since-bug` | `f5fa6ec` |
| `gauntlet-phase-2-3-2-prompt-section-extract` | `82b9ee4` |
| `gauntlet-phase-2-3-prompt-asbuilt` | `6f575d3` |
| `gauntlet-phase-3-1-capability-extractor` | `0794881` |
| `gauntlet-phase-3-4-matrix-scaffold` | `0d309f5` |
| `gauntlet-phase-6-1-dashboard-renderer` | `7290793` |
| `gauntlet-phase-6-3-launch-readiness` | `5445c5c` |
| `gauntlet-phase-7-9-canary-prep` | `cdafcca` |
| `gauntlet-phase-8-2-grandma-rewriter` | `026131f` |
| `gauntlet-phase-8-9-mechanical` | `bc2497d` |
| `harden-agent-10-read-on-waking` | `ce4c4df` |

Plus `feat/eternitas-cert-of-record`, deleted without a tag: it was **fully
merged** into master via PR #290 with 0 commits ahead, so its history is
already in `master`.

---

## How each was judged — and a method note that matters

**`git cherry` lied, and trusting it would have caused a real mistake.**

`git cherry` compares patch-ids. A squash merge rewrites the patch, so every
squash-merged commit looks brand new. It reported `harden/agent-10-read-on-waking`
as having 4 unmerged commits — four genuinely valuable Windows fixes to the
stress harness and the test suite. I was on the point of extracting them into a
PR.

Cherry-picking all four **conflicted**. That prompted a content-level check
instead of a patch-id one, comparing each touched file between `master` and the
branch tip:

```
scripts/marathon/analyze.py   identical
scripts/marathon/chaos.py     identical
scripts/marathon/faults.py    identical
scripts/marathon/run.py       identical
scripts/marathon/sliders.py   identical
tests/test_capability_undo_journal.py  identical
```

**All six files byte-identical to master.** Those Windows fixes had already
landed via a squash merge. Re-opening them as a PR would have been pure noise
built on a tool's false positive.

*Use content comparison, not `git cherry`, to decide whether a branch's work
already landed in a squash-merge repo.*

### The one branch that looked worth rescuing

`gauntlet/phase-2-2-b-since-bug` fixed a real user-visible bug — `/lifeboat`'s
`Since:` line never rendered because the code read key `"at"` where
`resurrect()` writes `"ts"`.

Checked against master directly. `src/windyfly/agent/resurrect.py:925` already
reads:

```python
"since": state.get("ts"),
```

with the explanatory comment intact. **Already fixed.** The branch's remaining
difference is only that it *predates* the Lifeboat FSM layer master now has —
i.e. the branch is behind, not ahead.

### The other thirteen

All 14 `gauntlet/phase-*` branches date from a single day, **2026-05-21** — 73
days before this triage — from a launch-gauntlet campaign the brief confirms
nobody is tracking. Their net-new content is scaffolding for that campaign:
`render_gauntlet_dashboard.py`, `extract_capabilities.py`, `grandma_rewriter.py`,
a 576-cell capability-matrix scaffold, canary timer units, and as-built docs
(`PROMPT_AS_BUILT.md`, `LIFEBOAT_FSM_AS_BUILT.md`, `RUNBOOK.md`,
`ESCALATION.md`).

None of it is wrong. All of it describes a codebase 73 days and one hatch
consolidation ago, and validating it against today's `master` is real work with
speculative payoff. Per the brief — *"a branch list nobody trusts is worth less
than a short one that is true"* — they are archived rather than resurrected.

The largest, `gauntlet/phase-2-3-2-prompt-section-extract`, is the one most
plausibly worth revisiting: it splits `agent/prompt.py` into 9 section modules
with contract tests. If prompt refactoring comes up again, start from
`archive/2026-08-02/gauntlet-phase-2-3-2-prompt-section-extract` rather than
from scratch.

---

## If you are adding a branch here

The reason this list got to 17 is that nothing ever closed the loop between
"branch pushed" and "work landed or abandoned". Two habits prevent the regrowth:

- **Open the PR when you push the branch**, even as a draft. An unmerged branch
  with no PR is invisible to every review and every future instance.
- **Delete on merge.** `gh pr merge --squash --delete-branch` does it in the
  same breath; `feat/eternitas-cert-of-record` sat merged-and-undeleted since
  2026-07-15 purely because that flag was omitted.
