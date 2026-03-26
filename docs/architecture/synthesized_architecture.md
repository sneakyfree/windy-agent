# Project Codename TBD — Unified Personal AI Agent Architecture
## Final Canonical Master Plan — March 2026
### Synthesized from Grok, Gemini, ChatGPT, Perplexity, Perplexity CM, and Antigravity (Opus 4.6)

---

## What This Document Is

This is the **final architectural blueprint** for building a from-scratch personal AI agent. Six AI reviewers across nine rounds have stress-tested every decision against the codebases of OpenClaw (336k stars, TypeScript, 20+ channels), Hermes Agent (13k stars, Python, self-improving memory), Mem0 (48k stars, Apache 2.0, vector + graph memory), and Letta/MemGPT (21k stars, OS-inspired tiered memory). This document is the locked canonical plan.

---

## The Vision

By 2030, personal AI agents scale from hundreds of thousands of users to billions. The winner will be the agent that:

1. **Feels like a lifelong companion** — witty, opinionated, emotionally attuned, remembers everything
2. **Never makes you start over** — Soul Continuity Engine with Soul Preview (user approves imports before any record is written)
3. **Gets measurably smarter** — "Never Wrong Twice" guarantee based on user friction events
4. **Works everywhere** — 20+ messaging channels, voice, devices, offline, local-first
5. **Costs less than $20/month** for normal users (transparent sliders to spend more for more)
6. **Is customizable like an iPhone** — User Control Panel ("The Cockpit") with presets, sliders, live cost/trade-off feedback
7. **Is trustworthy at its core** — truth layer with epistemic status; never silently overwrites
8. **Future-proofs with modular contracts** — 2028 breakthroughs plug in without rewrites

---

## Competitive Landscape (2026)

| Agent | Stars | Strengths | Weaknesses |
|---|---|---|---|
| **OpenClaw** | 336k | 22+ channel adapters, personality (SOUL.md), pluggable gateway | No structured memory, no self-improvement, no truth layer |
| **Hermes** | 13k | Self-improving skills, FTS5 memory, SQLite-first | Minimal personality, no WebSocket gateway, engineer-focused UX |
| **Mem0** | 48k | Multi-level memory scopes, version control, 49% LongMemEval | No agent runtime, no personality, no skills engine |
| **Letta/MemGPT** | 21k | OS-inspired tiered memory, self-editing memory | No channel adapters, complex architecture, narrow focus |
| **Us** | 0 | All of the above + Control Panel + truth layer + Soul Continuity | Nothing built yet |

**Key decision:** Evaluate Mem0's open-source layer (Apache 2.0) in Phase 0 as a potential drop-in for our nodes/edges memory system. If it saves 3–4 weeks without compromising our truth layer, use it. If not, our schema's `epistemic_status` + temporal validity + correctness classification is the explicit differentiator.

---

## Core Architecture: Dual-Runtime "Brain + Body"

| Layer | Runtime | Role | Why |
|---|---|---|---|
| **Gateway ("Body")** | TypeScript on Bun | Channels, WebSockets, voice, devices, UI | Best async I/O + every chat SDK |
| **Brain ("Mind")** | Python 3.12+ via uv | Memory, reasoning, skill evolution, orchestration | Python owns ML/AI libraries |
| **Shared Memory** | SQLite + sqlite-vec + FTS5 | Single source of truth | One file. Zero ops. Portable. Offline |
| **IPC Bridge** | MCP for external tools; Unix Domain Socket for internal brain↔gateway | Isolates internal stability from external tool quality | MCP ecosystem + clean fast-path |
| **Write Queue** | In-process Python queue inside Brain | All DB writes serialized, batched, prioritized | Zero extra processes in v1; extract to broker for multi-user |

**Why dual-runtime from day one?** Starting Python-only means rewriting channel adapters later. The IPC bridge is ~200 lines. Cost of splitting now ≈ zero; cost of migrating later is enormous.

**Why SQLite?** One `.db` file IS personal data sovereignty. WAL mode + single writer thread handles personal-scale writes. Write queue extends viability 10×+. Postgres migration = swap one backend in the queue.

**Why split IPC?** MCP is production-stable in 2026 (Linux Foundation standard) and perfect for external tools. But internal brain↔gateway calls need isolation: a flaky external MCP tool server must never stall the internal bridge. Unix Domain Socket with thin JSON envelope for internal calls (~100 lines). Both protocols coexist cleanly.

---

## The Database Schema

**Phase 0–1 Minimal Viable Schema (6 tables):**

```sql
-- 1. NODES: Entities in the life-graph
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    scope_id TEXT DEFAULT 'personal',          -- 'personal', 'work', 'family'
    type TEXT NOT NULL,                        -- 'person', 'project', 'preference', 'place'
    name TEXT NOT NULL,
    metadata JSON,
    -- TRUTH LAYER
    epistemic_status TEXT DEFAULT 'inferred',  -- 'user_asserted', 'inferred', 'verified', 'speculative', 'contradicted'
    confidence REAL DEFAULT 1.0,
    source TEXT DEFAULT 'inferred',           -- 'user_stated', 'agent_inferred', 'imported'
    verification_method TEXT,
    last_verified_at DATETIME,
    -- TEMPORAL FACTS
    valid_from TEXT,                           -- When this fact became true
    valid_until TEXT,                          -- When it ceased (NULL = current)
    -- DECAY
    decay_score REAL DEFAULT 1.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. EPISODES: Conversation & event history
CREATE TABLE episodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT,
    role TEXT NOT NULL,                        -- 'user', 'agent', 'system', 'tool'
    content TEXT NOT NULL,
    summary TEXT,
    token_count INTEGER,
    cost_usd REAL,
    emotional_context TEXT,                    -- 'neutral', 'stressed', 'excited', 'frustrated' (inferred)
    embedding BLOB,
    embedding_model TEXT,
    embedding_version INTEGER DEFAULT 1,
    last_accessed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3. SOUL: Personality, identity, control panel sliders
CREATE TABLE soul (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    key TEXT NOT NULL,                         -- 'personality', 'humor_level', slider keys, etc.
    value TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    source TEXT DEFAULT 'default',            -- 'imported', 'user_set', 'control_panel', 'agent_evolution'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 4. SKILLS: Versioned, self-improving code
CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    code TEXT NOT NULL,
    language TEXT NOT NULL,
    description TEXT,
    permissions_required JSON,
    risk_level TEXT DEFAULT 'low',
    eval_score REAL,
    eval_results JSON,
    promoted BOOLEAN DEFAULT FALSE,
    usage_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    parent_skill_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_used DATETIME
);

-- 5. FAILURES: "Never Wrong Twice" system
CREATE TABLE failures (
    id TEXT PRIMARY KEY,
    fault_type TEXT NOT NULL,                  -- 'factual_error', 'reasoning_error', 'execution_failure',
                                              -- 'preference_miss', 'ambiguity_mishandled', 'alignment_fault'
    description TEXT NOT NULL,
    root_cause TEXT,
    correction_action TEXT,
    correction_skill_id TEXT,
    improvement_verified BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME
);

-- 6. COST LEDGER: API spend tracking
CREATE TABLE cost_ledger (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    task_type TEXT,                            -- 'chat', 'background_reflection', 'skill_eval', 'sub_agent'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- SEARCH INDEXES
CREATE VIRTUAL TABLE episodes_fts USING fts5(content, summary, content='episodes');

-- PRAGMAS (set at connection time)
-- PRAGMA journal_mode=WAL;
-- PRAGMA synchronous=NORMAL;
```

**Phase 2–3 Additions (via migrations):**

```sql
-- EDGES: Knowledge graph relationships
CREATE TABLE edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES nodes(id),
    target_id TEXT NOT NULL REFERENCES nodes(id),
    relation TEXT NOT NULL,                    -- 'prefers', 'caused_by', 'led_to', 'blocked_by'
    strength REAL DEFAULT 1.0,
    confidence REAL DEFAULT 1.0,
    timestamp_weight REAL DEFAULT 1.0,
    source_weight REAL DEFAULT 1.0,
    decay_score REAL DEFAULT 1.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- INTENTS: User goals with decay
CREATE TABLE intents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    scope_id TEXT DEFAULT 'personal',
    description TEXT NOT NULL,
    status TEXT DEFAULT 'active',              -- 'active', 'completed', 'abandoned', 'paused'
    priority INTEGER DEFAULT 5,
    origin TEXT DEFAULT 'user_said',           -- 'user_said', 'inferred_from_chat'
    autonomy_policy TEXT DEFAULT 'inform',     -- 'do_for_me', 'inform', 'track_only'
    decay_score REAL DEFAULT 1.0,
    linked_nodes JSON,
    last_touched DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- CONFLICTS: Contradiction tracking
CREATE TABLE conflicts (
    id TEXT PRIMARY KEY,
    node_id TEXT REFERENCES nodes(id),
    old_value TEXT,
    new_value TEXT,
    resolution_status TEXT DEFAULT 'unresolved',
    user_resolved BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME
);

-- SOUL HISTORY: Personality versioning & rollback
CREATE TABLE soul_history (
    id TEXT PRIMARY KEY,
    soul_id TEXT NOT NULL REFERENCES soul(id),
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,                           -- 'user', 'agent_evolution', 'import'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## The Write Queue (Concurrency Solution)

An **in-process queue inside the Brain** — not a separate process. Single writer thread handles all DB writes.

```
[ Gateway (TS) ]  →  [ Brain (Python) ]
                          ↓
                   [ IN-PROCESS WRITE QUEUE ]  →  [ SQLite ]
                          ↑
                   Priority tags:
                   HIGH:   user messages, responses (immediate)
                   MEDIUM: memory updates, skill stats (batched every 5s)
                   LOW:    decay, re-embedding (trickled, droppable)
```

**Rules:**
- HIGH drained immediately; MEDIUM batched in transactions (`BEGIN; INSERT x100; COMMIT;`)
- Write coalescing: 5 rapid updates to same node → 1 final write
- Idempotency keys prevent duplication
- Backpressure: shed LOW tasks under load; never block user interaction on DB writes
- **Upgrade path:** Extract to external process when multi-user concurrency demands it (v3+)

---

## Security & Permissions (Layered, Phased)

### v1: Practical Security
- Skills declare permissions in YAML header (`filesystem:read`, `network:outbound`)
- Three risk levels: **low** (auto-allow), **medium** (soft confirm), **high** (explicit user approval)
- Docker sandbox for dynamically generated code (restricted FS, no root, timeout)
- Memory writes classified: `user_preference` (safe), `identity` (sensitive → confirmation), `long_term_commit` (requires review)
- **Audit-Before-Act:** At autonomy ≥ 8, agent shows a pre-flight manifest before multi-step autonomous tasks: "I'm going to: 1. Search X, 2. Draft Y, 3. Send Z — proceed?" One confirm for the whole chain.

### v2: Full Zero-Trust
- 4-layer capability model: Identity → Capability → Contextual Permission → Policy Engine
- Hard caps on recursion depth, token budget, time budget
- Full audit trail; execution tree for sub-agent actions

---

## The Ten Pillar Features

### 1. Soul Continuity Engine with Soul Preview
**Not blind auto-import.** Before writing a single record, the agent shows a structured summary: "Here's what I found. Here's what I could parse. Here's what I couldn't. Approve before I write anything." This IS the onboarding experience.

| Source | What | How |
|---|---|---|
| OpenClaw | SOUL.md, MEMORY.md, skills, config | Parse Markdown |
| Hermes | SQLite sessions, MEMORY.md, skills | Read SQLite + MD |
| ChatGPT | Conversation export (JSON) | Parse, no skills |
| Claude | Projects + conversations | Parse export |
| Mem0 | Memory graph export | Parse API/graph format |

**Identity Firewall:** Safe (auto-import) → Sensitive (import at confidence 0.5, surface for review) → Executable (sandbox + eval before promotion).

### 2. Truth-Aware Memory
- **Facts** (nodes) — `epistemic_status`, `confidence`, temporal validity, scoped contexts
- **Episodes** — summarized, embedded, with `emotional_context` (inferred: neutral/stressed/excited/frustrated)
- **Intents** (Phase 2) — origin tracking, intent decay, per-intent `autonomy_policy` (do_for_me / inform / track_only)
- **Cognitive Decay:** Active forgetting of stale/low-confidence data
- **Narrative Drift Detection:** Background loop periodically asks "What if my model of this user is wrong?" — samples low-confidence nodes, tests alternatives, surfaces to user
- **Contradiction Resolution:** Never silently overwrite. Log to `conflicts`. Agent asks: "You said X before, now Y — should I update?"

### 3. "Never Wrong Twice" System
**Definition of "wrong":** User friction events — corrections, contradictions, "that's not right," retries, explicit edits. Observable without asking. Logged automatically.

**Correctness classification** (not all "wrong" triggers learning):

| Fault type | Triggers rewrite? | Action |
|---|---|---|
| `factual_error` | ✅ Always | Update memory |
| `reasoning_error` | ✅ Always | Update skill/prompt |
| `execution_failure` | ✅ Always | Fix skill |
| `preference_miss` | ⚠️ Sometimes | Update soul, not skill |
| `ambiguity_mishandled` | ⚠️ Sometimes | Ask next time |
| `alignment_fault` | ❌ No | Update soul + nodes with high weight |

When the same friction pattern recurs, the agent surfaces it proactively rather than waiting to be corrected again.

### 4. Self-Improving Skill Engine
**v1:** Human-visible eval logs + manual promotion
**v1.5:** Automated syntax check + basic sandbox execution + human-reviewed golden tests
**v2:** Full three-gate eval (syntax → logic → safety → regression)

Skills are versioned for rollback. No skill goes live without passing gates.

### 5. Personality Engine (Core Identity + Adaptive Mode)
- Editable `SOUL.md` + JSON sliders
- **Core Identity** (stable): always present, never fully disabled — humor style, communication quirks, user knowledge
- **Adaptive Mode** (task-specific): constrained in neutral domains, not removed
- **Explicit mode signaling:** When shifting to focused mode, agent says so briefly: "Switching to focused mode for this." — eliminates uncanny valley
- **Personality as alignment filter:** traits influence planning and tool selection, not just tone
- **Personality versioning:** `soul_history` enables rollback without losing memory
- **Emotional awareness:** When `emotional_context = stressed` for 3+ consecutive messages → lower proactivity, shorter responses, hold off on new suggestions

### 6. Universal Channel Gateway
Port OpenClaw's MIT adapters: Telegram, Discord, WhatsApp, Slack, Signal, Matrix, Web, voice. New channel = one interface implementation.

### 7. Cost-Aware Autonomy
- Every LLM call logged to `cost_ledger`
- Agent asks before expensive operations: "This will cost ~$0.45, proceed?"
- Budget caps per task, per sub-agent, per day
- Background loops have hard token budgets

### 8. Sub-Agent Orchestration (Phased)
**v1:** Pseudo-sub-agents (same process, isolated context, depth limit = 1)
**v2:** Container-per-sub-agent with execution tree (who did what, cost per branch, where failure happened)

### 9. Offline-First with Graceful Degradation
- Local GGUF model (Ollama/llama.cpp) for basic tasks offline
- Complex tasks queued for connectivity
- Seamless cloud → local degradation

### 10. User Control Panel — "The Cockpit"
Transparent, iPhone-style customization with live cost/trade-off feedback. **The single biggest differentiator nobody else has.**

**Layer 1: Presets (80% of users — grandma-friendly)**
- 🎭 **The Buddy** — Fun, chatty, proactive. ~$15/mo.
- 🔧 **The Engineer** — Precise, efficient, lean. ~$8/mo.
- ⚡ **The Powerhouse** — Everything maxed. ~$30/mo.
- 🎛️ **Custom...** → Layer 2.

**Layer 2: Sliders (20% of users)**

| Slider | Controls | Cost |
|---|---|---|
| **Personality** (1–10) | SOUL.md depth, humor, playful asides | ~$0–3/mo |
| **Reasoning Depth** (1–10) | Chain-of-thought, verification passes | ~$0–5/mo |
| **Memory Depth** (1–10) | Episodes/nodes pulled per turn | ~$0–8/mo |
| **Proactivity** (1–10) | Background loops, nudges, morning briefs | ~$0–5/mo |
| **Autonomy** (1–10) | How much agent does without asking | $0 (risk, not cost) |
| **Verbosity** (1–10) | Output length | ~$0–2/mo |
| **Epistemic Strictness** (1–10) | Speculate freely (1) ↔ only verified facts (10) | $0 |

**Live feedback:** Estimated monthly cost updates in real time. Pie chart showing context window allocation. Plain-English tooltips. "Reset to sane defaults" button. Recommended safe ranges.

**Why competitors can't easily copy:** Requires `cost_ledger`, modular personality injection, and two-stage thinking pipeline. For OpenClaw/Hermes, it's a full-stack rewrite. For us, it's a settings page.

---

## Intent Tracking UX (The "Intent Inbox" Pattern)

Intent tracking must feel like a co-pilot confirming the flight plan, not surveillance.

**Rules:**
1. Every inferred intent surfaces in an **Intent Inbox** within 24 hours — user confirms, edits, or dismisses
2. Agent never acts on an inferred intent without surfacing it first
3. Confirmed intents appear in a **"My Goals"** view the user controls
4. One-tap **"forget this goal"** from any reminder
5. Stale intents auto-downrank via `decay_score` and surface in monthly **"Goals Hygiene"** review
6. Each intent has `autonomy_policy`: **do_for_me** / **inform** / **track_only**
7. Weekly **Alignment Briefing**: "Here's what I think you're working toward — confirm, adjust, or discard"

---

## "Invisible Desires" — Ranked by Impact × Feasibility

| # | Feature | Source | Phase |
|---|---|---|---|
| 1 | **Soul Continuity + Soul Preview** (import with user approval) | Grok + Perplexity CM | v1 |
| 2 | **User Control Panel / "The Cockpit"** (sliders + live cost) | Antigravity | v1 |
| 3 | **"Never Wrong Twice"** (visible self-correction via friction events) | ChatGPT + Perplexity CM | v1 |
| 4 | **Intent Memory + Intent Inbox** (goals with decay, 24h surfacing) | ChatGPT + Perplexity CM | v1 |
| 5 | **Truth Layer** (epistemic status, correctness classification) | ChatGPT | v1 |
| 6 | **Cost-Aware Planning** ("This will cost $0.45") | Gemini | v1 |
| 7 | **Emotional Awareness** (inferred context adjusts tone/proactivity) | Grok + Perplexity CM | v1 |
| 8 | **Audit-Before-Act** (pre-flight manifest for high-autonomy chains) | Perplexity CM | v1 |
| 9 | **Explicit Mode Signaling** ("Switching to focused mode") | Perplexity CM | v1 |
| 10 | **Offline-First Mode** (local model fallback) | All | v1 |
| 11 | **Narrative Drift Detection** ("What if my model of you is wrong?") | ChatGPT R2 | v2 |
| 12 | **"Explain Me to Myself"** (periodic self-reflection) | ChatGPT | v2 |
| 13 | **Execution Tree** (who did what, cost per branch, failure trace) | ChatGPT R2 | v2 |
| 14 | **Context Cache Layer** (pre-computed context bundles) | ChatGPT R2 | v2 |
| 15 | **Personality Versioning + Slow Drift Warning** (monthly drift report) | Perplexity + CM | v2 |
| 16 | **Channel-Scope Mapping** (work Slack → only work scope) | Perplexity R2 | v2 |
| 17 | **"Forget Me Now" / Scheduled Deletion** (privacy controls) | Perplexity R2 | v2 |
| 18 | **Cross-Model Consensus** (poll second model for high stakes) | Gemini | v2 |
| 19 | **Context Pre-fetching** (watches active window, pre-loads) | Gemini R2 | v2 |
| 20 | **Family/Team Shared Context** (scope-based, role-based privacy) | Grok + Perplexity | v2 |
| 21 | **Trust Dashboard** (what agent believes, confidence, why) | ChatGPT | v2 |
| 22 | **Recursive Tool Building** (writes missing tools) | ChatGPT | v2 |
| 23 | **"Silent Competence"** (fixes small things, logs transparently) | ChatGPT | v2 |
| 24 | **Decision Replay** ("Why did I choose that last year?") | ChatGPT R2 | v3 |
| 25 | **Versioned Self** ("What was I like 2 years ago?") | ChatGPT R2 | v3 |
| 26 | **Life Chapters & Narrative Export** | Perplexity | v3 |
| 27 | **Generative UI / No-Code App Factory** | Grok | v3 |
| 28 | **Skill Marketplace** (community-shared, reputation-scored) | Grok | v3 |
| 29 | **Inter-Agent Federation** (MCP/ACP/A2A) | All | v3 |
| 30 | **Physical World Integration** (IoT, AR, robotics) | Grok | v3 |

---

## Build Roadmap (Corrected Sequencing)

| Phase | What | Timeline | Key Deliverable |
|---|---|---|---|
| **0** | **Python agent loop ONLY** — message in → memory query → model call → episode write → response out. Mem0 evaluation spike. | Week 0–1 | Working agent that holds a conversation (Day 5) |
| **1** | Bun gateway adapter + UDS bridge + Soul Continuity (with Soul Preview) + SOUL.md parser | Week 2–4 | Import from OpenClaw/Hermes, agent "knows you" |
| **2** | Channel adapters (Telegram, Discord, Web) + personality engine + **Control Panel** | Week 4–6 | Reachable on 3+ platforms with sliders |
| **3** | Skill engine + manual eval + cost ledger + failure logging + intent system + edges/graph | Week 6–10 | Skills evolve, spend tracked, "Never Wrong Twice" live |
| **4** | Pseudo-sub-agents + offline mode + cognitive decay + conflict resolution | Week 10–14 | Specialist spawning, works offline |
| **5** | Dashboard, observability, automated eval, personality versioning, cross-model consensus | Week 14–18 | Full transparency layer |

---

## NOT Building in v1

- ❌ External write broker process (in-process queue sufficient)
- ❌ Postgres / pgvector / graph DB
- ❌ Full zero-trust security (simplified permissions first)
- ❌ Full edges/intents tables (Phase 2–3 migration)
- ❌ AR/robotics/IoT
- ❌ Custom web dashboard (TUI + simple web viewer first)
- ❌ Community marketplace
- ❌ WASM sandboxing (Docker/seccomp first)
- ❌ GPU hypervisor / compute sovereignty
- ❌ Agent mesh networking

---

## Tech Stack Summary

| Layer | Choice |
|---|---|
| **Gateway** | TypeScript on Bun |
| **Brain** | Python 3.12+ with uv |
| **Memory** | SQLite + sqlite-vec + FTS5, WAL mode, scoped contexts |
| **Write Queue** | In-process Python queue with priority tags, batched transactions |
| **External IPC** | MCP (JSON-RPC) for tools and federation |
| **Internal IPC** | Unix Domain Socket with thin JSON envelope |
| **Skills format** | YAML frontmatter + markdown body |
| **Config** | TOML (single file) |
| **Sandbox** | Docker + seccomp (v1), Firecracker (v2+) |
| **Observability** | OpenTelemetry + SQLite event log |
| **Personality** | Editable SOUL.md + versioned sliders + alignment filter + task-mode overrides + mode signaling |
| **Control Panel** | Presets + sliders + pie chart stored in `soul` table |
| **Embedding** | 768-dim default, configurable per purpose, lazy re-embed on access |

---

## Attribution

| Reviewer | Key Contributions |
|---|---|
| **Grok** (2 rounds) | Soul Continuity concept, personality spectrum, market positioning |
| **Gemini** (3 rounds) | SQL schema foundation, lazy embedding migration, personality-as-alignment-filter, UDS transport |
| **ChatGPT** (2 rounds) | Truth layer, intent memory, write broker, correctness classification, narrative drift, core identity vs mode |
| **Perplexity** (2 rounds) | Scope partitions, temporal facts, personality versioning, intent decay, channel-scope mapping, per-intent autonomy |
| **Perplexity CM** (1 round) | Soul Preview, build sequencing fix, Mem0 competitive analysis, user friction events, emotional_context, audit-before-act |
| **Antigravity** (all rounds) | Control Panel concept, lean stack discipline, phased roadmap, synthesis and critique, "not in v1" discipline |
