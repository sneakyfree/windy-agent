# Wave 4 — Write Hands Design

**Status:** Design draft. No code yet. Awaiting Grant's decisions on the open
questions below before implementation.

**Scope:** First write-class capabilities for the agent — `fs.write_file`,
`fs.move_file`, `fs.delete_file`, plus the supporting **undo log** and
**dry-run** infrastructure that anything destructive lives on top of.

**Why now:** Wave 3 #1 (#56) shipped `fs.read_file` + `fs.list_directory` —
the agent can see the user's machine but can't act on it. Wave 4 turns the
agent from "knows where your repo is" into "can edit your repo." This is
the inflection from chat-with-search to agent-that-does-work.

**Architectural anchors that constrain this design:**

- **Tier system from `descriptor.py`** — `WRITE_LOCAL_SAFE` (Tier 2,
  USER+, dry-run available) and `WRITE_DESTRUCTIVE` (Tier 3, TRUSTED+,
  undo mandatory) already exist. Wave 4 has to honor them, not redefine.
- **`agent_actions` ledger from #53** — already has `parent_action_id` and
  `outcome_score` columns reserved. Wave 4 should use them, not add new ones.
- **CapabilityDenied → typed-error path from #50** — already routes to a
  user-facing message. Wave 4 errors should classify into the existing
  taxonomy (`TOOL_FAILURE`, `DB_FAILURE`, etc.), not invent new ones unless
  there's a strong reason.

---

## Decision 1: Undo log strategy

How does an `fs.delete_file` call become reversible?

**Option A — Append-only journal of (action, original_state).** Every
destructive capability writes a JSON record to `~/.windy/undo-journal.ndjson`
with the action type, target path, original contents (for write/delete),
original location (for move), and a UUID. A `fs.undo_last_action` capability
reads the journal tail and reverses. Pros: one mechanism for every action
type. Cons: original_state can be huge (a deleted 100MB file lives twice on
disk for the journal retention window).

**Option B — Per-file snapshots in `~/.windy/snapshots/`.** Before any
destructive write, copy the original to a content-addressed snapshot dir
(`<sha256>/<basename>.bak`). Action ledger references the snapshot id. Undo
re-materializes from the snapshot. Pros: deduped storage if the same file is
edited many times. Cons: orphaned snapshots accumulate; needs a sweeper.

**Option C — Git-shadow repo at `~/.windy/shadow/<workspace_id>/.git`.**
Mirror the workspace's tree as bare git commits triggered before each
destructive op. Undo = `git revert HEAD`. Pros: standard tooling, free
diff/log/blame, branchable. Cons: heavyweight for small instances; couples
the agent to git semantics; doesn't extend to non-file actions later
(`email_send` undo can't use git).

**Recommendation: Option A (append-only journal).** Cleanest single-mechanism
for every undoable action — including future non-file actions like
`message_send` (whose "original_state" is "no message was sent" → undo posts
a retraction). Cap the journal at 30 days × 100MB and rotate to compressed
archives. Bypass the journal write only when the user explicitly opts out
per-call (`{"skip_undo": true}` in args, gated behind OWNER band).

**Schema:** new file `~/.windy/undo-journal.ndjson`, one JSON object per line:

```json
{
  "id": "01JE3...",
  "action_id": "<agent_actions.id>",
  "capability_id": "fs.delete_file",
  "target": "/Users/grant/notes/old.md",
  "original_state": {
    "kind": "file",
    "content_b64": "<base64>",
    "size": 4823,
    "mode": 33188,
    "mtime": 1745251200
  },
  "applied_at": "2026-04-21T03:14:15Z",
  "expires_at": "2026-05-21T03:14:15Z"
}
```

**Open question for Grant:** OK with the file-system journal (no DB column),
or should this go in a new `agent_undo_log` SQLite table for queryability?
File-system journal is simpler; table is more queryable.

---

## Decision 2: Confirmation prompts

Does `fs.delete_file` block on user confirmation before acting?

**Option A — Always confirm anything Tier 3+.** Every destructive op posts
a confirmation message to the active channel and waits for `yes` / `no`.
Pros: maximally safe. Cons: kills agent autonomy ("Grant said clean up the
notes folder, why is the agent asking 47 times?").

**Option B — Threshold-based.** No confirm for ≤N (e.g., 3) destructive
ops in a single agent turn; confirm above that. Pros: small ops feel fluid,
big ops feel safe. Cons: arbitrary cutoff; can be defeated by chunking.

**Option C — Trust-band-driven.** SANDBOX never gets destructive (already
true via tier). USER always confirms. TRUSTED confirms above threshold N.
OWNER never confirms (relies on undo). Pros: maps cleanly to who's asking.
Cons: OWNER can still fire "delete -rf ~/" by mistake.

**Recommendation: Option C with a per-capability override.** Map confirm
behavior to the band:

| Band | Default for Tier 3+ |
|------|---|
| SANDBOX | denied (tier gate) |
| USER | confirm every call |
| TRUSTED | confirm if >3 ops in turn OR target outside `~/projects` |
| OWNER | never confirm; undo is the safety net |

Per-capability override via a `confirm_threshold` field on the descriptor
so a future `fs.delete_recursively` can force confirm even at OWNER band.

**Open question for Grant:** Should there be a global "panic mode" toggle
(in `windyfly.toml` or a slash command) that forces confirmation at every
band for the next N minutes — useful when you're letting the agent run
unattended and want extra paranoia?

---

## Decision 3: Dry-run UX

How does the LLM (or user) preview "this would delete 47 files"?

**Option A — Dry-run as a separate capability call.** Caller invokes
`fs.delete_file_dryrun` → returns `{"would_delete": ["a.md", "b.md", ...],
"total_bytes": 12000}`. Then `fs.delete_file` actually does it. Pros:
explicit, no special args. Cons: doubles the tool surface.

**Option B — Dry-run as an arg flag.** Single capability, `dry_run=true`
arg. Returns the would-do envelope without acting. Same handler walks the
same code path with a guard at the act-step. Pros: minimal surface, single
codepath = correctness by construction. Cons: LLM might forget the flag.

**Option C — Always dry-run first; agent loop wraps.** The capability
dispatcher in `agent/loop.py` automatically calls every Tier 3+ capability
with `dry_run=true` first, posts the envelope to the user, then calls again
without dry_run. Pros: invariant. Cons: every destructive call costs 2
invocations; loop logic gets gnarly.

**Recommendation: Option B (arg flag).** Keep the tool surface tight. Ensure
correctness by extracting the "what would happen" computation into a helper
that both branches share:

```python
def _plan_delete(path: str, allowed_roots: list[str]) -> dict:
    """Returns {target, exists, size, would_orphan: [list]} regardless of
    whether we're dry-running or actually deleting."""
```

The handler:

```python
def delete_file(*, path: str, dry_run: bool = False) -> dict:
    plan = _plan_delete(path, allowed_roots)
    if dry_run:
        return {"plan": plan, "executed": False}
    _record_undo(plan)
    os.remove(plan["target"])
    return {"plan": plan, "executed": True}
```

The `dry_run` field appears in the capability's `input_schema` so the LLM
knows about it. The capability descriptor's `dry_run_supported = True`
declares it for future surfaces (e.g., `/caps` listing).

**Dry-run envelope shape (for all destructive caps):**

```json
{
  "plan": {
    "action": "delete_file",
    "target": "/Users/grant/notes/old.md",
    "exists": true,
    "size_bytes": 4823,
    "side_effects": ["removes 1 file", "frees 4.8 KB"]
  },
  "executed": false,
  "preview_only": true
}
```

**Open question for Grant:** Should the agent loop *force* a dry-run-then-
confirm flow for the first N destructive calls in a fresh session
(regardless of band)? Acts as a "warming up to autonomy" gradient.

---

## Decision 4: Tier classification per capability

Where do the three Wave 4 capabilities sit in the tier system?

| Capability | New file? | Recommendation |
|---|---|---|
| `fs.write_file` (path doesn't exist) | yes | **Tier 2** (`WRITE_LOCAL_SAFE`) |
| `fs.write_file` (path exists, overwriting) | no | **Tier 3** (`WRITE_DESTRUCTIVE`) |
| `fs.move_file` | n/a | **Tier 3** (destination collision is destructive) |
| `fs.delete_file` | n/a | **Tier 3** |

**Recommendation:** treat write_file as a *single capability* whose
internal logic checks `os.path.exists(target)` and refuses to overwrite
unless `overwrite=true` is passed. When `overwrite=true`, treat the call as
Tier 3 dynamically (audit + undo log). When `overwrite=false` (default), it's
Tier 2 (no undo needed, no overwriting risk).

The capability descriptor stays at `tier=Tier.WRITE_LOCAL_SAFE` (the lower
tier — the safer default), and the runtime escalation when `overwrite=true`
happens inside the dispatcher: if the args include `overwrite=true`, the
capability's effective `band_required` bumps from USER to TRUSTED. This is
the first capability to need *runtime tier escalation* — Wave 5's
`shell_exec` will need similar logic for things like `--force` flags.

**Implementation hook:** add a `runtime_tier_check(args) -> Tier | None`
optional callback to `Capability`. Default returns None (no escalation).
Write-file's callback returns `Tier.WRITE_DESTRUCTIVE` when `overwrite` is
true. The dispatcher consults it after the static band check passes.

**Open question for Grant:** Comfortable with runtime tier escalation as a
first-class concept on Capability descriptors, or would you rather split
write_file into two distinct caps (`fs.create_file` Tier 2, `fs.overwrite_file`
Tier 3)? Two-cap split is more explicit; runtime escalation is more
ergonomic.

---

## Decision 5: Failure modes and partial actions

What happens when `fs.write_file` runs out of disk halfway through, or a
batch of moves hits a permission error on file 4 of 7?

**Recommendation — atomic writes + recorded partial outcome:**

- All `fs.write_file` calls write to a `<target>.windy.tmp` first, then
  `os.rename` (atomic on the same filesystem). Rollback on rename failure.
- Batch ops (Wave 4 #2 will introduce `fs.write_files_batch`) record
  per-file success/failure in the audit ledger's existing `error_message`
  field as JSON: `{"succeeded": [...], "failed": [{"path": "...", "error": "..."}]}`.
- Partial-completion is itself a recorded outcome state — the audit row's
  `success` flag becomes a tri-state: 1 = all good, 0 = total failure,
  2 = partial (the column type is INTEGER so the values widen cleanly).
- `outcome_score` gets populated: 1.0 for full success, 0.0 for total
  failure, `succeeded/(succeeded+failed)` for partial. This is the first
  signal Wave 7's optimizer can use.

**Open question for Grant:** OK with the success column going tri-state, or
would you rather add a separate `partial` boolean column? Tri-state keeps
the schema slim; separate column is more explicit for SQL queries.

---

## Scope: what's in Wave 4 #1 vs. deferred

**Wave 4 #1 (the next implementation PR after Grant's design review):**

- `fs.write_file` (with overwrite-as-runtime-escalation per Decision 4)
- `fs.move_file`
- `fs.delete_file`
- `fs.undo_last_action` (reads the journal, reverses, marks undone)
- The `~/.windy/undo-journal.ndjson` infrastructure
- The runtime tier escalation hook on `Capability`
- Tests: per-capability happy path, dry-run, undo, partial failure, atomic
  rename failure recovery, allowlist enforcement, always-deny enforcement
  (inherited from Wave 3 #1)

**Deferred to Wave 4 #2:**

- `fs.write_files_batch` (multi-file atomic write with rollback)
- `fs.move_files_batch`
- `fs.delete_directory` (recursive — needs special confirmation)
- Journal sweeper (background task pruning expired entries)

**Deferred to Wave 4 #3 (read parity completion):**

- `git.log`, `git.diff`, `git.status` (read-only git ops, on top of Wave 3
  allowlist)
- `email.list_threads`, `email.read_thread` (IMAP read, separate auth path)

**Deferred to Wave 5+:** anything that touches a remote (`git.push`,
`email.send`, `gh.create_pr`) or escapes the filesystem allowlist (`shell`).

---

## Open questions for Grant — decision matrix

| # | Decision | Default if you don't decide |
|---|---|---|
| 1 | Undo log: filesystem journal or DB table? | filesystem journal |
| 2 | Panic-mode global confirm toggle? | not built |
| 3 | First-N-calls-of-session forced dry-run? | not built |
| 4 | Runtime tier escalation OR split into two capabilities? | runtime escalation |
| 5 | success column tri-state OR separate partial column? | tri-state |

If you want a different default on any of these, leave a PR comment with
your preference. If you bless all defaults, the next move is Wave 4 #1
implementation.
