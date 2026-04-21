# Wave 6 — Multi-Agent Collaborators Design

**Status:** Design draft. No code yet. Awaiting Grant's decisions on the
open questions below before implementation.

**Scope:** Replace the depth-1, one-shot, no-memory `sub_agent` from
`agent/sub_agents.py` with **long-running named collaborators** that:
- Have their own persistent identity (a "research" collaborator, a
  "writing" collaborator)
- Optionally share filtered slices of the parent's memory
- Communicate via task-post / result-reply (not free-form chat)
- Show up in the audit ledger via the existing `parent_action_id` column
  added in #53

**Why this beats Hermes:** their `delegate_task` is depth-2 max with
zero parent-memory inheritance — every subagent starts cold. OpenClaw's
multi-agent is *deterministic routing across isolated agents* (different
problem — fan-in routing, not delegation). We can ship the first agent
framework where collaborators have **continuity** with the parent and
each other, gated by per-collaborator memory-share permissions.

**Architectural anchors:**

- **`agent_actions.parent_action_id` from #53** — already reserved for
  this. Collaborator actions link back to the parent action that spawned
  them.
- **Tier system from `descriptor.py`** — collaborator delegation is itself
  a capability (`agent.delegate_to`); ships at `Tier.EXTERNAL_EFFECT` (4)
  because spawning a collaborator costs LLM tokens and time = real
  external resources.
- **`outcome_score` column from #53** — Wave 7's optimizer will use
  collaborator success rate as a key signal: "the research collaborator
  has a 73% success rate vs. inline LLM's 81% on similar tasks."

---

## Decision 1: Lifecycle

When does a collaborator come into existence and when does it stop
existing?

**Option A — Spawn-on-demand, persist-for-session.** First time the parent
agent invokes `agent.delegate_to(name="research", task="...")`, a
collaborator named "research" is created with its own DB rows for
soul/memory. It persists for the duration of the session (telegram chat
session, matrix room session). Next session, it's gone (cold start
again).

**Option B — Persist-forever (named collaborators are first-class entities).**
Collaborators live in the soul table just like the agent itself. "research"
exists across sessions, accumulates memory, has personality drift. Grant's
agent at `windy-0.toml` and grandma's agent at `windy-grandma.toml` each
have their own collaborator pool.

**Option C — Time-bounded (e.g., 7 days of inactivity → archived).**
Persists across sessions but garbage-collects unused collaborators.

**Recommendation: Option B (persist-forever).** This is the win
condition vs. Hermes — *continuity* of the collaborators is the moat. A
"research" collaborator that's been around for 3 weeks knows your
research preferences (what depth of detail, what formatting, what sources
you trust) better than one spawned fresh per task. The cost is one row
per collaborator in the soul table; trivial.

The collaborator's own audit history accumulates; Wave 7 optimizer can
ask "is the research collaborator getting better over time?" and detect
collaborator drift just like personality drift.

Add a new table `collaborators`:

```sql
CREATE TABLE collaborators (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,                   -- "research", "writing", etc.
    parent_user_id TEXT NOT NULL,         -- the soul this collaborator belongs to
    persona_prompt TEXT NOT NULL,         -- what kind of collaborator they are
    band Band NOT NULL DEFAULT 'USER',    -- their own band (usually = parent's, can be lower)
    memory_share_policy TEXT NOT NULL,    -- see Decision 2
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_used_at DATETIME,
    use_count INTEGER DEFAULT 0,
    archived_at DATETIME                  -- nullable; for soft-delete
);
```

**Open question for Grant:** Persist-forever (Option B) or time-bounded
(Option C with 7-day inactive sweep)? Persist-forever is the strategic
moat; time-bounded is more operationally simple.

---

## Decision 2: Memory sharing

How does a collaborator see (or not see) the parent's memory?

**Option A — No sharing (Hermes default).** Each collaborator gets only
the task prompt and its own private memory. Pro: clean isolation. Con:
loses the moat.

**Option B — Full sharing.** Collaborator sees everything in the parent's
nodes, episodes, intents. Pro: maximum continuity. Con: every
collaborator becomes a different angle on the same memory; potential
prompt-injection attack surface (a malicious task could have the
collaborator surface secrets).

**Option C — Per-collaborator opt-in topic filter.** When creating a
collaborator, declare what node types and topics it can see:

```
agent.delegate_to(
    name="research",
    task="...",
    memory_filter={
        "node_types": ["research_topic", "source", "citation"],
        "include_personality": True,
        "include_intents": False,
        "topic_keywords": ["Polly", "mortgage", "rate sheets"],
    },
)
```

**Recommendation: Option C (per-collaborator opt-in topic filter).**
Memory-share policy becomes a property of the collaborator, set at
creation time and updatable by the parent agent (with confirmation
prompt). Default for a fresh collaborator is `{"include_personality":
True, "node_types": [], "topic_keywords": []}` — they share the
personality (so the collaborator "feels" like part of the agent) but no
factual memory until the parent explicitly grants topics.

This is the pattern no competitor has. It maps cleanly to the band
system: a "guest" collaborator might run with band=USER even when the
parent is OWNER, limiting what *capabilities* it can call regardless of
what memory it can see.

**Schema for `memory_share_policy` field:** JSON with the shape above.

**Open question for Grant:** OK with the topic-filter approach, or do
you want something simpler (single boolean `share_memory: true|false`)?
Topic filter is more powerful but more cognitive load.

---

## Decision 3: Communication protocol

How does the parent talk to a collaborator and get a result back?

**Option A — Task-post / result-reply (RPC-style).** Parent calls
`agent.delegate_to(name, task, ...)`, blocks until collaborator returns
a single string result. The collaborator's internal LLM loop runs end-
to-end before returning.

**Option B — Continuous chat thread.** Parent and collaborator have an
ongoing conversation; messages flow back and forth. The parent's call to
`agent.send_to_collaborator(name, message)` is non-blocking; results
arrive as separate tool calls in the parent's loop.

**Option C — Streaming result.** Parent calls `agent.delegate_to` and
gets a stream of partial responses. Useful for long collaborations where
the parent might want to redirect mid-flight.

**Recommendation: Option A (task-post / result-reply, blocking RPC).**
The simplest mental model and the easiest to reason about for the
audit log. The parent sees a single tool call (`agent.delegate_to`) and
gets a single result. The collaborator's internal turn-by-turn LLM
exchanges show up as child rows in `agent_actions` linked via
`parent_action_id`.

For multi-turn collaboration, the parent makes multiple sequential
`agent.delegate_to` calls — the collaborator's persistent memory
preserves context between calls.

Streaming and continuous-chat are real future needs but would
fundamentally complicate the dispatcher and the audit shape. Defer to
Wave 6 #3+.

**Open question for Grant:** OK with blocking RPC as v1, or do you want
streaming from day one? Streaming costs ~3x the implementation
complexity.

---

## Decision 4: Naming

Are collaborators named explicitly by the parent agent, or auto-named
per task?

**Recommendation: explicit names, suggested by the parent LLM.** The
parent decides "I want a research collaborator" and that becomes a
persistent entity. Two patterns the parent can use:

- **Pattern 1 — by role:** `research`, `writing`, `code-review`, `email`,
  `triage`. Long-lived, accumulate role-specific knowledge.
- **Pattern 2 — by project:** `polly-clone-research`, `nachocrunch-pricing`.
  Long-lived per-project, then archived when the project wraps.

Both patterns share the same `collaborators` table; just naming
conventions. The agent's persona prompt should nudge toward role-based
naming for general work and project-based naming when working on a
specific deliverable.

**Auto-naming (collaborator-1, collaborator-2)** is rejected — it
defeats the continuity moat. If the parent doesn't name the
collaborator, the task should run inline rather than spawn one.

**Open question for Grant:** Should there be a hard cap on number of
collaborators per parent (e.g., 20)? Past a certain count, naming
discipline breaks down and we just have "many similar collaborators."
Recommend a soft warning at 10, hard cap at 50.

---

## Decision 5: Resource budgeting

How do we prevent a runaway collaborator (or a parent that spawns 100 of
them) from burning the daily budget?

**Recommendation:**

- **Per-collaborator turn token budget:** default 8000 tokens per
  delegation call (matches the agent's own `max_context_tokens`). Configurable
  per-collaborator via the `collaborators.persona_prompt` policy section.
- **Per-collaborator daily $ cap:** new column `daily_budget_usd` on
  `collaborators`, default $1.00. Independent of the parent's daily
  budget (which still applies aggregated).
- **Aggregation into parent budget:** every collaborator call cost rolls
  up to the parent's `cost_ledger`. The collaborator's spend counts
  against the parent's daily budget too, not just its own.
- **Recursion limit:** collaborators *cannot* spawn further collaborators
  in v1. Hard depth cap at 1. Multi-level delegation is real but adds
  whole new failure modes (deadlock, circular delegation, infinite
  recursion). Defer to Wave 6 #2 if it proves necessary.

**Open question for Grant:** OK with the recursion=1 cap? Hermes also
caps at 2 (parent → child). If we ship 1, we're more conservative; can
loosen later without breaking anyone.

---

## Decision 6: Failure modes

What happens when:

- **Collaborator hangs** (LLM doesn't return): same wall-clock cap as
  shell — 30 seconds default per delegation, raise `TimeoutError` →
  routes through #50 classifier.
- **Collaborator raises:** the parent gets a JSON error envelope, can
  retry or work around. Audit row marks collaborator's row as failed,
  parent's row as success (the *capability* call succeeded — the
  collaborator failed *via* the capability).
- **Collaborator returns nonsense:** v1 doesn't try to detect this.
  The parent's LLM is responsible for evaluating whether the result is
  useful. Wave 7's optimizer will eventually score collaborator outputs
  against parent intent satisfaction.
- **Collaborator contradicts the parent's memory:** if shared memory is
  enabled, the collaborator might write a node value that conflicts with
  the parent's existing belief. The existing `conflicts` table from
  migration 2 catches this; the parent gets a follow-up turn where the
  conflict is surfaced.

**Open question for Grant:** Should collaborator-raised errors be
retried automatically (1 retry with the same task) before surfacing to
the parent's LLM? Hermes does no retry; we'd be more aggressive. Recommend
no auto-retry — keeps the LLM in control of "should we try again."

---

## Decision 7: Audit

How does collaborator activity show up in `agent_actions`?

**Recommendation:** every collaborator's *internal* capability invocations
land as agent_actions rows with:

- `parent_action_id` = the parent's `agent.delegate_to` action ID
- `session_id` = parent's session_id with a suffix (e.g.,
  `telegram:8545:collab:research`)
- `user_id` = parent's user_id (the collaborator belongs to the user)

This means `/pulse` automatically picks up collaborator activity; the
existing `capability_success_rate` query splits cleanly by parent vs.
collaborator. No new schema needed beyond Decision 1's `collaborators`
table.

**Open question for Grant:** Should the parent's audit row record a
*summary* of what the collaborator did (which capabilities it called,
total cost), or just the final result string? Summary is more useful for
optimizer training; final-string-only is simpler.

---

## Scope: what's in Wave 6 #1 vs. deferred

**Wave 6 #1:**

- `collaborators` table (migration 6)
- `agent.delegate_to` capability (Tier 4, EXTERNAL_EFFECT)
- `agent.list_collaborators` capability (Tier 1, READ_EXTERNAL — the
  parent can ask "who do I have to work with?")
- `agent.create_collaborator(name, persona, memory_filter)` capability
  (Tier 4 — collaborator creation is itself a costly external effect)
- `agent.archive_collaborator(name)` capability (Tier 3)
- Internal `_run_collaborator_turn(...)` that drives a collaborator's own
  LLM loop with a filtered memory view
- Tests: lifecycle, memory filter, recursion cap, parent_action_id linking

**Deferred to Wave 6 #2:**

- Streaming results
- Multi-level recursion
- Auto-retry on collaborator failure
- Conflict-detection follow-up turn

**Deferred to Wave 6 #3+:**

- Cross-instance collaborator sharing (Grant's "research" collaborator on
  his Mac shows up on his iPhone instance — needs Wave 8's fleet sync)

---

## Open questions for Grant — decision matrix

| # | Decision | Default if you don't decide |
|---|---|---|
| 1 | Persist-forever or time-bounded collaborators? | persist-forever |
| 2 | Topic-filter memory share or simple boolean? | topic-filter |
| 3 | Blocking RPC or streaming v1? | blocking RPC |
| 4 | Hard cap on collaborator count? | soft warn at 10, hard cap at 50 |
| 5 | Recursion depth cap of 1 OK? | yes (1) |
| 6 | Auto-retry on collaborator failure? | no auto-retry |
| 7 | Audit row records summary or just final string? | summary |

If you bless all defaults, Wave 6 #1 is mechanical from this design plus
the `_run_collaborator_turn` core (~250 lines of glue around the
existing agent loop with the memory-filter applied).
