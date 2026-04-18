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
