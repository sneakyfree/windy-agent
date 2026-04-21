# CLAUDE.md

Project-scoped instructions for Claude Code agents working in
`windy-agent`.

## Branching Policy

This repo follows Grant's ecosystem-wide Branching Policy: feature
branches + PR review, **no direct pushes to `master`**. See
`~/.claude/projects/-Users-thewindstorm/memory/feedback_branching_policy.md`.

### Exception log

Exceptions to the Branching Policy are always narrow and one-time.
Every exception must be logged here with a reason and a scope.

- **2026-04-17 — `wave-7-batch-only`.** Bucket A of
  `docs/MERGE_TRIAGE.md` (PRs #2, #11, #14) is authorized to self-merge
  as part of the Wave 7 batch-merge pass. Scope: the three Bucket A
  PRs only, for this batch only, squash-and-delete-branch. Does NOT
  extend to Buckets B–E. Does NOT extend to future waves or future
  self-merges. Buckets B merges require manual smoke; Bucket C stops
  for review; Bucket D surfaces decisions; Bucket E defers.
- **2026-04-17 — doc-only direct commits to master.** Triage and
  decision documents (`docs/MERGE_TRIAGE.md`, `docs/BUCKET_C_REVIEW_REQUEST.md`,
  `docs/BUCKET_D_DECISIONS.md`) are authorized to commit direct
  to `master` without a PR. Scope: these three files, for this
  batch only. Code changes remain PR-only.
- **2026-04-21 — Wave 15 #0 instance-config split (direct-to-master).**
  Removal of `windy-0.toml` and `scripts/run-windy-0.sh` from this repo
  is authorized as a direct-to-master commit. Reason: instance-specific
  files were leaking into the generic codebase; relocating them to
  `~/windy-0-soul/` honors the architectural model (windy-agent =
  generic class; per-instance soul repo = config + launcher + identity).
  Scope: the deletion only. New copies live in the soul repo. Bot
  verified booting clean on the new launcher path before commit.

## Architectural rule (post-Wave 15 #0)

This repo must contain **no instance-specific files** (no `<name>.toml`,
no `scripts/run-<name>.sh`, no per-instance hardcodes). Each Windy Fly
instance keeps its config + launcher in its own soul repo (e.g.,
`sneakyfree/windy-0-soul`). The agent codebase here is the generic
"class"; soul repos are the "instances." Future instances should clone
this repo untouched and clone their own `<name>-soul` repo for everything
specific to them.
