# Wave 14 — Kernel Foundation: Tracing Spine + Boot Sequence

> Pre-paid insurance for the 5-year vision. Two kernel-level changes that compound across every future capability/channel/plane.

---

## Why now

Last turn (2026-04-21) Grant asked the strategic question: how do we architecturally enhance Windy Fly for long-term performance, stability, and ambidexterity? The right answer is **OS-ification** — turn the closed Python codebase into an open architecture where capabilities, channels, providers, and planes are loaded from manifests.

That vision is multi-year work. But two kernel changes are **pre-paid insurance** that get exponentially more expensive every week we wait:

1. **Tracing spine** — every action gets a UUID that flows through every plane. Cheap to add now; impossible to retrofit cleanly when there are 47 capabilities and 8 channels.
2. **Boot sequence abstraction** — codify the boot order in one place so missing-step bugs surface loudly. We already paid interest on this when capability registrations were missing in the telegram branch but present in matrix.

Both are additive, backward-compatible, low-risk. They do not change the agent's behavior; they change its observability and its bootability. They make every Wave 15+ feature 10× cheaper to build, debug, and maintain.

Multi-tenancy schema scaffolding (the third "do now" item from last turn) is **deferred to Wave 15** — it's higher risk, touches every memory table, and benefits from a fresh-Windy review rather than being rushed at end-of-session.

---

## Wave 14a — Tracing spine

### What it is

A `request_id` UUID generated at the entry of every user-facing operation (`agent_respond`, slash command, channel inbound). Stored in a `contextvars.ContextVar` so it propagates without plumbing. Attached to:
- Every log record via a `logging.LoggerAdapter` or filter
- Every persisted DB row in observability-relevant tables
- The user-facing `report_id` (currently a 6-char hex; tied to the request_id so support can correlate)

### Schema changes

Add `request_id TEXT` column (nullable, indexed) to:
- `events` — every structured event already has metadata; gets `request_id` for correlation
- `agent_actions` — capability invocation audit ledger; gets `request_id` to tie a capability call back to the originating user message
- `episodes` — user/assistant message rows; gets `request_id` so you can pull "every row created during request abc123"
- `cost_ledger` — every LLM call cost; ties cost back to the originating request
- `journal_entries` — reflective entries; nice-to-have correlation

`nodes`, `intents`, `conflicts` get `request_id` too if cheap, but those are derivative of episodes — lower priority. Start with the five above.

Index: `CREATE INDEX IF NOT EXISTS idx_<table>_request_id ON <table>(request_id)` so trace lookups are fast.

Migration is idempotent: `ALTER TABLE … ADD COLUMN request_id TEXT` is harmless if column already exists (we'll wrap with a "if not exists" check via `PRAGMA table_info`).

### Code changes

New file `src/windyfly/agent/tracing.py`:
- `request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)`
- `set_request_id() -> str` — generates UUID4, sets the contextvar, returns it
- `get_request_id() -> str | None` — convenience
- `WindyLogAdapter` or filter that includes `request_id` on every log record (truncated to 8 chars for readability: `[req:abc12345]`)

Wire-up:
- `agent_respond` calls `set_request_id()` at entry
- Channel inbound handlers also call `set_request_id()` for top-level visibility into commands/non-conversation paths
- The `report_id` shown to user (in `channels/errors.py`) is derived from `get_request_id()` truncated to 6 chars

DB writes:
- `save_episode`, `log_event`, `record_action`, `log_cost` accept an optional `request_id` parameter that defaults to `get_request_id()` so callers don't need to thread it explicitly

### What "done" looks like

- Run any user message → grep the log for one `req:abc12345` token → see every step the request touched, in order
- Query `SELECT * FROM agent_actions WHERE request_id = 'abc12345'` → see every capability the request invoked
- Query `SELECT * FROM cost_ledger WHERE request_id = 'abc12345'` → see total cost of that request
- The user-facing `report_id` (e.g., `err:abc123`) prefixes the request_id so support correlation is trivial

### Effort + risk

- **Effort**: 3-4 hours
- **Risk**: Low. Additive columns (NULL default), additive logging fields, no behavior change.
- **Rollback**: `git revert`. Schema columns left in place (harmless).

---

## Wave 14b — Boot sequence abstraction

### What it is

A `BootSequence` class in `src/windyfly/agent/boot.py` that captures the canonical boot order as a list of named steps. Each step is a callable + dependency declaration. `main.py` hands the BootSequence to a runner that invokes steps in order, logs progress, and aborts loudly on failure.

### Why

The bug we already hit: capability registrations were missing in `main.py`'s telegram branch but present in matrix. Silent failure — bot ran but tools were absent. The cause: `main.py` is two parallel ad-hoc procedural sequences that drift over time. The fix is to extract the canonical sequence to one place and have both branches call it.

### The canonical sequence

```python
sequence = BootSequence([
    Step("config.load",                load_config),
    Step("db.open",                    open_database),
    Step("db.migrate",                 run_migrations),
    Step("write_queue.start",          start_write_queue),
    Step("memory.seed_check",          maybe_seed_first_run),
    Step("scheduler.start",            start_decay_drift_backup),
    Step("capabilities.audit_hooks",   install_audit_hooks),
    Step("capabilities.filesystem",    register_filesystem),
    Step("capabilities.shell",         register_shell),
    Step("capabilities.collaborators", register_collaborators),
    Step("capabilities.network",       register_network),
    Step("channels.register",          register_channels),
    Step("channels.start",             start_channels),
])
sequence.run(context)
```

Each `Step` has:
- `name`: stable id for logging (`config.load`, `capabilities.shell`, etc.)
- `runner`: callable taking the boot context (`Context` dict-like with `config`, `db`, `write_queue`, `registry`, etc.)
- `optional`: bool — if true, failure is logged but doesn't abort
- `requires`: list of step names — declared dependencies (run-time check, not topological sort)

### What "done" looks like

- Both telegram and matrix branches in `main.py` reduce to: build context, build sequence, run sequence, hand off to channel manager
- Adding a new capability registration = adding one Step to the canonical sequence (one place, both channel branches benefit automatically)
- Boot log shows: `[boot] step config.load OK 12ms` … `[boot] step capabilities.shell OK 8ms` … `[boot] complete in 2.3s` — readable timeline
- If any step fails: `[boot] step capabilities.shell FAILED: <error> — aborting boot` and the process exits with a clear message

### Effort + risk

- **Effort**: 2-3 hours
- **Risk**: Medium. Refactors `main.py` (the boot path). If I screw up, bot won't start. Mitigated by: keeping the Step runners as thin wrappers over existing functions, running the bot manually after the change, full pytest pass before commit.
- **Rollback**: `git revert` — the old main.py is two functions away.

### Tests

- `tests/test_boot_sequence.py`:
  - `test_steps_run_in_declared_order`
  - `test_failed_required_step_aborts_boot`
  - `test_failed_optional_step_continues`
  - `test_missing_dependency_raises`
  - `test_step_timing_recorded`

---

## Wave 15 (deferred — next session)

- Multi-tenancy schema scaffolding (`tenant_id` columns + scoped queries)
- Manifest-driven capability loader
- Channel adapter interface formalization
- Snapshot/restore primitive

These are higher-risk, higher-scope, and benefit from a fresh-Windy review.

---

## Acceptance criteria for Wave 14

- [ ] `git log` shows two clean commits: tracing spine, boot sequence
- [ ] Full pytest suite green for {agent_loop, provider_failover, confabulation_guard, boot_sequence, tracing} — no regressions in other suites
- [ ] Bot restarts cleanly on the branch
- [ ] One Telegram round-trip succeeds (send `/status`, get a real reply, see `req:` token in the log)
- [ ] Branch pushed; PR opened against master with this doc as the description
- [ ] `~/windy-0-soul/TURNOVER.md` updated to point at Wave 14

---

## Out of scope (resist scope creep)

- New capabilities (skill library, browser, SSH, etc.) — Wave 15+
- Channel adapter formalization — Wave 15
- Memory plane refactor — Wave 16+
- Provider plane unification — Wave 17
- Sandbox tier gradient enforcement — when a real bug forces it
