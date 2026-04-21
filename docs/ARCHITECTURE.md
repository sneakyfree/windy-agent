# Windy Fly Architecture — the 10-Plane Personal AI OS

> **Status:** authoritative as of Wave 3 #1 (PR #56). Updated when wave
> sequence advances or strategic framing shifts.
>
> **Audience:** new contributors orienting cold. Skip to your area of
> work via the table of contents — the file's long because it captures
> the *why* behind every choice, but you don't need to read it linearly.

---

## Table of contents

1. [Category — what Windy Fly is and isn't](#1-category)
2. [The 10 planes](#2-the-10-planes)
3. [The Capability Plane in depth](#3-the-capability-plane-in-depth)
4. [Wave sequence](#4-wave-sequence)
5. [Strategic differentiation](#5-strategic-differentiation)
6. [Things explicitly NOT to do](#6-things-explicitly-not-to-do)
7. [Where to start as a contributor](#7-where-to-start-as-a-contributor)

---

## 1. Category

**Windy Fly is a Personal AI Operating System, not an agent framework.**

This single sentence dictates every architectural decision in the repo.
The wrong frame is "we are building a better agent framework that
competes with OpenClaw and Hermes." The right frame is "we are building
a different *category* — an OS-like layer on which agent capabilities
live."

| Framework-shaped (OpenClaw, Hermes) | OS-shaped (Windy Fly) |
|---|---|
| Kit of capabilities developers wire | Kernel of subsystems shipped together |
| Audience: "people who'd build their own AI" | Audience: "everyone who wants AI in their life" |
| Security is a layered-on opt-in | Security is the choke point everything routes through |
| Memory is a side store | Memory is a first-class plane |
| Personality is "you write your own prompt" | Personality is a versioned, drift-detected subsystem |
| Stability is a baby giraffe | Stability is owned by the Resilience Plane |

When a design question comes up, the framing test is: *would an OS make
this an opt-in? a kit would; an OS wouldn't.* Trust gating, audit
logging, capability metadata, cost tracking — these are all OS
properties shipped together, not bolt-ons.

**Brand split:**

- **Windy Fly** — the product (closed/SaaS/personal)
- **Hi Fly** — the OSS engine (Chromium-to-Chrome relationship)
- **windy-agent** — the dev-time repo name; both products build from
  this code

---

## 2. The 10 Planes

Every aspect of the system reorganizes into these ten subsystems. Each
owns one concern and exposes a stable interface; each evolves on its
own cadence.

### 2.1 Identity Plane

**Owns:** passport (Eternitas integration), bands, soul, drift,
lineage, lifecycle ceremonies (hatching).

**Status:** strong. The passport-band system is the unique strategic
moat — see Section 5.

**Key code:**
- `src/windyfly/birth_certificate.py` — agent's identity proof
- `src/windyfly/soul_import/` — import personality from OpenClaw / Hermes
- `src/windyfly/personality/` — soul, sliders, drift detection
- `src/windyfly/eternitas/` — passport client
- `src/windyfly/trust/` — band-aware trust gate

### 2.2 Perception Plane

**Owns:** channel adapters, message ingestion, attention/priority,
multi-channel session continuity.

**Status:** strong. 13 channel adapters; ChannelManager unifies the
inbound / outbound shape.

**Key code:**
- `src/windyfly/channels/` — base, manager, adapters per platform
  (cli, matrix, telegram, sms, slack, discord, signal, irc, whatsapp,
  teams, email, …)

**Future:** voice (STT) channel, vision (image input) channel, file
attachment ingestion.

### 2.3 Memory Plane

**Owns:** episodes, nodes (graph), intents, journal entries, decay,
retrieval. The agent's "inner life."

**Status:** strong; the breadth and depth here is what beats Hermes
decisively. Eight tables in SQLite with FTS5; cognitive decay on a 24h
cycle; priority-batched write queue; emotional context per episode.

**Key code:**
- `src/windyfly/memory/database.py` — schema migrations
- `src/windyfly/memory/episodes.py` — conversation history
- `src/windyfly/memory/nodes.py` — knowledge graph
- `src/windyfly/memory/intents.py` — user goals
- `src/windyfly/memory/decay.py` — cognitive decay daemon
- `src/windyfly/memory/write_queue.py` — async batched writes

**Future:** activate sqlite-vec for semantic retrieval; cross-instance
memory sync; memory provenance UI.

### 2.4 Judgment Plane

**Owns:** prompt assembly, model selection, tool re-loop, planning,
parallel exploration. The thinking step.

**Status:** adequate; needs auto model routing (cheap-for-triage,
big-for-hard) and parallel-exploration optimizer hooks.

**Key code:**
- `src/windyfly/agent/loop.py` — `agent_respond` core ReAct cycle
- `src/windyfly/agent/prompt.py` — assemble personality + memory + msg
- `src/windyfly/agent/models.py` — `call_llm` with provider failover
  and cooldown circuit breaker (Wave 1 #46)
- `src/windyfly/agent/providers.py` — OpenAI/Anthropic/Z.AI/etc registry

### 2.5 Capability Plane (load-bearing)

**Owns:** tool descriptors with band, sandbox tier, cost class, scope,
reversibility, audit, rate limit, dry-run, undo. Every "hand" the
agent has plugs in here.

**Status:** scaffolded in Wave 2 (#52–#55), first hands shipped in
Wave 3 #1 (#56). This is the architectural innovation. See Section 3.

**Key code:**
- `src/windyfly/agent/capabilities/descriptor.py` — `Capability`
  dataclass + `Band`/`Tier`/`SandboxTier`/`Reversibility` enums
- `src/windyfly/agent/capabilities/registry.py` — `CapabilityRegistry`
  + module-level singleton + `invoke_sync` adapter
- `src/windyfly/agent/capabilities/audit.py` — pre/post invoke hooks
  that write to `agent_actions`
- `src/windyfly/agent/capabilities/filesystem.py` — `fs.read_file`,
  `fs.list_directory` (Wave 3 #1)

### 2.6 Action Plane

**Owns:** sandbox dispatch, retries, side-effect ledger, timeouts.
The doing step.

**Status:** partial. Sandbox tier strings exist in `descriptor.py`;
real Docker dispatcher comes in Wave 5. The `agent_actions` ledger
(Wave 2 #2 / #53) is the audited side-effect record that everything
will eventually write to.

**Key code:**
- `src/windyfly/memory/agent_actions.py` — ledger writers + queries
- `src/windyfly/skills/sandbox.py` — current Python/Node subprocess
  sandbox (limited; Wave 5 expands this)

### 2.7 Learning Plane

**Owns:** outcome optimizer, skill curation, failure→correction
linking, drift correction.

**Status:** substrate exists (cost ledger, agent_actions, failures,
emotional context all in the same SQLite); optimizer doesn't ship
until Wave 7. The unique angle: training fitness will be **(intent
satisfaction × cost × emotional delta)** — a three-dimensional signal
that competitors don't have because they don't track all three in one
store.

**Key code (substrate):**
- `src/windyfly/memory/cost_ledger.py` — token + USD per call
- `src/windyfly/memory/agent_actions.py` — capability outcomes (Wave 2 #53)
- `src/windyfly/memory/intents.py` — user-goal status
- `src/windyfly/agent/emotion_detector.py` — per-episode emotional context

### 2.8 Safety Plane

**Owns:** trust gate, dry-run, undo log, blast-radius, anomaly
detection, rate limits.

**Status:** trust gate exists; the rest builds out across Waves 4–5.

**Key code:**
- `src/windyfly/trust/gate.py` — `require_trust_sync(action)` choke
  point that bands gate
- `src/windyfly/trust/check.py` — passport-band resolution

### 2.9 Resilience Plane

**Owns:** per-channel reconnect/backoff, provider failover, process
supervisor, self-ping, /health, circuit breakers, log redaction.

**Status:** Wave 1 (#45–#51) shipped a complete pass. Bot is
launchd-supervised, secrets are redacted, telegram has heartbeat +
exponential backoff, providers have a cooldown circuit breaker, /pulse
returns live diagnostics.

**Key code:**
- `src/windyfly/channels/telegram_bot.py` — resilience pattern
  (heartbeat, backoff, error handler) — Wave 1 #45
- `src/windyfly/agent/models.py` — provider failover chain — Wave 1 #46
- `scripts/install-windy-0-service.sh` — launchd supervisor — Wave 1 #47
- `src/windyfly/observability/redact.py` — log secret redaction — Wave 1 #51

### 2.10 Cost Plane

**Owns:** cost ledger, daily/monthly budgets, per-capability/channel/
skill attribution, burndown alerts, auto-downgrade.

**Status:** ledger + budget enforcement exist; per-capability
attribution lands when capabilities start reporting cost (Wave 2 #4
infrastructure is in place; capabilities just need to populate the
field).

**Key code:**
- `src/windyfly/memory/cost_ledger.py` — write API + queries
- `src/windyfly/memory/cost_tracker.py` — budget enforcement at
  agent_respond entry

---

## 3. The Capability Plane in depth

This is the load-bearing innovation. Every "hand" the agent has — and
will have — plugs into a single slot here.

### 3.1 The Capability descriptor

```python
@dataclass(frozen=True)
class Capability:
    id: str                    # "fs.read_file"
    description: str           # what the LLM sees as the tool description
    handler: Handler           # sync or async function
    input_schema: dict         # JSON Schema for the args
    name: str = ""             # human-friendly display name (defaults to id)

    tier: Tier = Tier.PURE_COMPUTE     # the LLM-friendly risk class

    # Policy fields — None means "use the tier default"
    band_required: Band | None = None
    sandbox_tier: str | None = None
    reversibility: str | None = None
    audit_required: bool | None = None
    cost_class: str | None = None
    dry_run_supported: bool | None = None
    undo_supported: bool | None = None

    rate_limit: str | None = None      # e.g., "100/hour" (enforcement TBD)
    scope: str = ""                    # free-form scope description
```

The `tier` is the LLM-friendly summary; the policy fields default from
the tier and can be individually overridden.

### 3.2 The Band hierarchy

```python
class Band(IntEnum):
    SANDBOX = 0   # unknown sender, demo, normie pre-pairing
    USER = 1      # verified user with passport (grandma after pairing)
    TRUSTED = 2   # power user (Grant after device pairing)
    OWNER = 3     # instance owner — the human who set the agent up
```

`IntEnum` so the gate check is `session_band >= cap.band_required` —
no string comparison gymnastics. Aligns with the band names already
used in `trust/gate.py`.

### 3.3 The Tier system

| Tier | Examples | Default band | Default sandbox | Default audit |
|---|---|---|---|---|
| 0 — PURE_COMPUTE | dice, calc, translate | SANDBOX | none | off |
| 1 — READ_EXTERNAL | web search, read_file, list_dir | USER | host_readonly | on |
| 2 — WRITE_LOCAL_SAFE | write_file (new), draft_email | USER | host_rw | on, dry-run available |
| 3 — WRITE_DESTRUCTIVE | delete, move, git commit | TRUSTED | host_rw | on, undo mandatory |
| 4 — EXTERNAL_EFFECT | send email, post msg, git push | TRUSTED | host_rw | on |
| 5 — FULL_MACHINE | shell exec, install pkg | TRUSTED | docker | on |

Every defaults table lives in `descriptor.py:defaults_for_tier(tier)`.

### 3.4 The killer method: `tool_schemas_for_band(band)`

```python
schemas = capability_registry.tool_schemas_for_band(session.band)
# schemas now contains only capabilities the session is allowed to call.
# Emit them to the LLM as the tools list — done.
```

**Lower-band sessions never even *see* high-tier tools in their LLM
context.** Same code, different band at boot, different exposed tool
surface. This is the inversion-of-control that no competitor has — they
ship the same tool list to every caller and fight it at execution time.

Concretely: grandma's instance ships with band=USER as default; her
LLM tool list contains Tier 0/1 capabilities. Grant's instance ships
with band=OWNER; his LLM tool list contains everything up to Tier 5.
**No code fork. Same registry. Different band at instance boot.**

### 3.5 Audit by construction

Every capability invocation through `capability_registry.invoke()` (or
`invoke_sync()` for sync callers) lands a row in `agent_actions`:
- `started_at` row from the pre-invoke hook
- `ended_at` / `success` / `error_class` / `duration_ms` from the
  post-invoke hook
- Args JSON-redacted (Telegram tokens, sk- API keys, etc.) before
  storage

Caps with `audit_required=False` (Tier 0 — pure compute) skip the
ledger entirely so `dice.roll` doesn't fill the table.

### 3.6 Author guide

See `docs/CAPABILITY_AUTHOR_GUIDE.md` for the recipe. The TL;DR:

1. Pick a tier
2. Write a handler (sync or async)
3. Build a Capability descriptor
4. Register in a `register_*_capabilities(registry, config)` function
5. Hook into `main.py` after `install_audit_hooks(...)`
6. Tests mirroring `tests/test_capability_filesystem.py`

---

## 4. Wave sequence

The waves are *delivery cohorts*, not strict dependencies. Each wave
ships a coherent slice; later waves build on earlier ones but each PR
within a wave is independently reviewable.

| Wave | Theme | Status |
|---|---|---|
| 1 — Resilience | Stability for daily dogfood | Complete (#44–#51, draft) |
| 2 — Capability Plane | Descriptor + audit + dispatch | Complete (#52–#55, draft) |
| 3 — Read-only hands | fs.read_file, list, glob, grep, git read | #56 + #58 + #59 draft; git read pending |
| 4 — Write hands | fs.write_file, move, delete, undo log | Design draft `docs/wave4-write-hands.md` |
| 5 — Shell + browser | shell.exec Docker-by-default, browser via Playwright | Design draft `docs/wave5-shell-exec.md` |
| 6 — Multi-agent | Long-running named collaborators | Design draft `docs/wave6-multi-agent.md` |
| 7 — Outcome optimizer | DSPy/GEPA-style on three-dim fitness | Substrate ready, optimizer pending |
| 8+ | Voice/vision, fleet sync, MCP/ACP | Future |

---

## 5. Strategic differentiation

### vs. OpenClaw (TypeScript, ~25 channels, multi-agent gateway)

OpenClaw's strength: **channel breadth and deterministic multi-agent
routing in a single local-first gateway**. We don't try to match that
breadth; we beat them where they're weak:

| Axis | OpenClaw | Windy Fly |
|---|---|---|
| Channels | ~25 | 13 (sufficient) |
| Multi-agent | deterministic routing across isolated agents | persistent collaborators with shared memory (Wave 6) |
| Memory depth | workspace files | 8-table SQL graph + FTS + decay |
| Personality | static persona file | sliders + drift + emotion-adaptive |
| Cost tracking | ✗ | per-call ledger + budget enforcement |
| Security default | host exec, plaintext secrets in `auth-profiles.json` | passport-band capability gating, redacted secrets |
| Self-improvement | ✗ | "Never Wrong Twice" + Wave 7 optimizer |

### vs. Hermes Agent (Python, Nous Research, self-improving)

Hermes' strength: **the self-improvement loop with a separate
DSPy/GEPA optimizer over agent-curated skills**. Genuinely
differentiated. We beat them where they're weak:

| Axis | Hermes | Windy Fly |
|---|---|---|
| Tool surface | huge (terminal, browser CDP, vision, MCP, ACP) | growing through Wave 3+ |
| Sandboxing default | local + click-prompt (their SECURITY.md tells you to switch off the default) | Docker by default (Wave 5) |
| Multi-agent | depth-2 fork, no memory inheritance | persistent collaborators with topic-filtered memory share (Wave 6) |
| Memory | FTS5 + Honcho + skills | 8-table + FTS + decay + cost + emotion |
| Personality | persona file | sliders + drift + emotion-adaptive |
| Optimizer fitness | task success | **(intent × cost × emotional delta)** — three-dim |
| Cost tracking | unclear | explicit per-call ledger |

The *category* moat: Windy Fly is a product, Hermes is a framework.
Hermes is "really good shell scripts." Windy Fly is "macOS for AI."

---

## 6. Things explicitly NOT to do

These are documented refusals — past discussions made the call and
shouldn't be re-litigated without surfacing the strategic frame.

- **Don't chase OpenClaw's 25-channel count.** 13 is enough; chasing
  more is a forever-tax against more important work.
- **Don't expose MCP/ACP before Wave 8.** Premature surface; we'd be
  exposing internals before they're stable.
- **Don't ship hands before Wave 2's Capability Plane.** Becomes the
  thing we're trying to beat.
- **Don't replace SOUL.md persona with skill-agent framing.** The
  brand is the brain.
- **Don't let "hands" become the marketing headline.** Cognitive
  layer is the moat. Hands are table stakes.
- **Don't add features, refactor, or introduce abstractions beyond
  what the task requires.** A bug fix doesn't need surrounding
  cleanup. Three similar lines is better than a premature
  abstraction.
- **Don't add error handling or fallbacks for impossible scenarios.**
  Trust internal code and framework guarantees. Only validate at
  system boundaries.
- **Don't write comments explaining WHAT the code does.**
  Well-named identifiers do that. Comments should explain WHY
  (hidden constraint, subtle invariant, workaround for a specific
  bug).

---

## 7. Where to start as a contributor

**If you're adding a new tool / hand:** read
`docs/CAPABILITY_AUTHOR_GUIDE.md`. Don't add to the legacy
`src/windyfly/tools/` directory; new work goes in
`src/windyfly/agent/capabilities/<your_namespace>.py`.

**If you're touching the agent loop:** start with
`src/windyfly/agent/loop.py:agent_respond` — that's the central ReAct
cycle. The structure is documented in the docstring at line 49.

**If you're touching memory:** start with
`src/windyfly/memory/database.py` for the schema and
`src/windyfly/memory/write_queue.py` for the async-write pattern. All
writes go through the queue; never call `db.execute()` from the agent
loop directly.

**If you're touching channels:** start with
`src/windyfly/channels/base.py` for the `ChannelAdapter` interface
and look at `telegram_bot.py` (Wave 1 #45) for the resilience pattern
every channel should follow (heartbeat, backoff, error handler).

**If you're touching trust / identity / passport:** start with
`src/windyfly/trust/gate.py` and the corresponding band table in
`descriptor.py`. The Capability Plane is the consumer; trust is the
gatekeeper.

**If you're working on an entire wave:** the design docs in `docs/`
are your spec. `docs/wave4-write-hands.md`, `docs/wave5-shell-exec.md`,
and `docs/wave6-multi-agent.md` each surface decisions that need
Grant's blessing before implementation begins.

**Branching policy:** see `~/.claude/projects/.../feedback_branching_policy.md`
in user memory or read recent PRs for the convention. Short version:
feature branches off the right parent (master for independent work,
prior-PR-branch for stacked work), draft PRs always, no direct pushes
to master.

---

## Appendix: file map

```
src/windyfly/
├── agent/
│   ├── capabilities/      # NEW (Wave 2): the Capability Plane
│   │   ├── descriptor.py  # Capability, Band, Tier, etc.
│   │   ├── registry.py    # CapabilityRegistry + invoke_sync
│   │   ├── audit.py       # pre/post hooks → agent_actions
│   │   └── filesystem.py  # fs.read_file, fs.list_directory (Wave 3)
│   ├── loop.py            # agent_respond — central ReAct cycle
│   ├── models.py          # call_llm, provider failover (Wave 1 #46)
│   ├── providers.py       # provider registry (OpenAI, Anthropic, Z.AI, ...)
│   ├── prompt.py          # prompt assembly
│   ├── emotion_detector.py
│   ├── intent_detector.py
│   ├── failure_detector.py  # "Never Wrong Twice"
│   └── sub_agents.py      # legacy depth-1; replaced by Wave 6
├── channels/              # Perception Plane
│   ├── base.py            # ChannelAdapter interface
│   ├── manager.py         # ChannelManager
│   ├── errors.py          # typed error classifier (Wave 1 #50)
│   └── *.py               # telegram, matrix, slack, discord, ...
├── memory/                # Memory + Cost Plane
│   ├── database.py        # schema + migrations
│   ├── write_queue.py     # priority queue daemon
│   ├── episodes.py / nodes.py / intents.py / cost_ledger.py
│   ├── agent_actions.py   # NEW (Wave 2 #53)
│   └── decay.py           # cognitive decay daemon
├── trust/                 # Safety Plane
│   ├── gate.py            # require_trust_sync choke point
│   └── check.py           # passport-band resolution
├── observability/
│   ├── events.py
│   └── redact.py          # NEW (Wave 1 #51)
├── personality/
│   ├── engine.py
│   └── versioning.py      # drift detection
├── eternitas/             # Identity Plane (passport client)
├── soul_import/           # OpenClaw + Hermes import
├── commands/              # slash commands
│   ├── core.py            # status, doctor, /pulse, /caps, ...
│   └── registry.py
├── tools/                 # legacy tools (pre-Capability-Plane)
│   └── *.py               # web_search, weather, todos, ...
├── main.py                # entry point + channel branches
└── config.py              # TOML config loader

docs/
├── ARCHITECTURE.md        # this file
├── CAPABILITY_AUTHOR_GUIDE.md
├── wave4-write-hands.md
├── wave5-shell-exec.md
├── wave6-multi-agent.md
└── DEPLOY.md / CHANGELOG.md / ...

scripts/
├── install-windy-0-service.sh   # launchd supervisor
├── uninstall-windy-0-service.sh
├── run-windy-0.sh
└── windy-0.env.example          # secrets template

tests/
├── test_capability_*.py    # Wave 2 + Wave 3 capability tests
├── test_provider_failover.py
├── test_error_classifier.py
├── test_log_redaction.py
└── ...
```
