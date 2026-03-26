# Greenfield Agent: Learning from OpenClaw & Hermes

## Where They're Strong, Where They're Weak

### OpenClaw

| Area | Verdict | Details |
|---|---|---|
| Gateway / Control Plane | 🟢 **Strong** | Clean 4-layer WebSocket daemon. Separation of Brain vs Muscles is elegant. Production-tested. |
| Channel Adapters | 🟢 **Strong** | 8+ messaging platforms with deep, platform-native adapters (Baileys, grammY, Bolt). Best in class. |
| Skill Ecosystem | 🟢 **Strong** | ClawHub marketplace with thousands of community skills. Publishing workflow is polished. |
| Multi-Repo Architecture | 🟡 **Mixed** | 18 repos gives modularity but creates discoverability problems and version coordination headaches. |
| Memory System | 🔴 **Weak** | Flat `MEMORY.md` files + daily logs. No search, no structure, no learning. Doesn't scale past a few weeks of use. |
| Self-Improvement | 🔴 **Weak** | Doesn't exist. The agent never gets better at repeated tasks. |
| User Modeling | 🔴 **Weak** | None. Every session treats the user like a stranger. |
| Tool Extensibility | 🟡 **Mixed** | Built-in tools are solid but the extension model is tightly coupled to the TypeScript runtime. |
| Testing & Eval | 🔴 **Weak** | No built-in eval framework. No way to measure agent quality over time. |

### Hermes Agent

| Area | Verdict | Details |
|---|---|---|
| Memory System | 🟢 **Strong** | SQLite/FTS5 with LLM summarization. Cross-session recall actually works. |
| Self-Improving Skills | 🟢 **Strong** | Closed learning loop: task → skill → improvement. The killer feature. |
| User Modeling | 🟢 **Strong** | Honcho dialectic modeling builds deep user understanding over time. |
| RL Integration | 🟢 **Strong** | Atropos integration for training data gen and RL experiments. Unique advantage. |
| Subagent System | 🟢 **Strong** | Isolated subagent spawning for complex workflows. |
| Gateway / Routing | 🔴 **Weak** | Single `gateway/run.py` script. Not a real control plane. No WebSocket API. |
| Channel Adapters | 🟡 **Mixed** | Supports major platforms but adapters are less mature than OpenClaw's. |
| Skill Ecosystem | 🟡 **Mixed** | ~40 bundled skills, `agentskills.io` standard, but no marketplace or community discovery layer. |
| Front-End / UI | 🔴 **Weak** | TUI only. No web dashboard, no companion apps, no visual interface. |
| Deployment Simplicity | 🟡 **Mixed** | Many backends (Docker, SSH, Modal) but setup is more complex than OpenClaw's one-liner. |

---

## What a From-Scratch Agent Could Improve

### 1. Architecture: Event-Driven Microkernel

**Problem with both:** OpenClaw's Gateway is monolithic Node.js. Hermes is a monolithic Python process. Neither handles graceful degradation — if the process dies, everything dies.

**Better approach:**
```
┌──────────────────────────────────────────┐
│              Event Bus (Core)             │
│  (Lightweight message broker — NATS,     │
│   Redis Streams, or embedded)            │
├──────────┬───────────┬───────────────────┤
│ Channels │  Agent    │  Memory           │
│ Service  │  Runtime  │  Service          │
│ (any     │  (any     │  (any             │
│  lang)   │   lang)   │   lang)           │
├──────────┼───────────┼───────────────────┤
│ Skills   │  Eval     │  User Model       │
│ Service  │  Service  │  Service          │
└──────────┴───────────┴───────────────────┘
```

Each service is an independent process that communicates via the event bus. Services can be:
- Written in **any language** (TypeScript for channels, Python for ML, Rust for performance-critical bits)
- **Restarted independently** without losing state
- **Scaled horizontally** (run 3 agent runtimes behind a load balancer)
- **Swapped out** (don't like the memory system? Replace just that service)

### 2. Memory: Tiered Memory Architecture

**Problem with both:** OpenClaw's flat files don't scale. Hermes's FTS5 is better but still single-tier — everything gets the same treatment.

**Better approach — three tiers like human memory:**

| Tier | What | Storage | Retention |
|---|---|---|---|
| **Working Memory** | Current conversation context, active tool state | In-process RAM | Session lifetime |
| **Episodic Memory** | Past conversations, task outcomes, user interactions | SQLite/FTS5 + vector embeddings | Indefinite, with decay scoring |
| **Semantic Memory** | Learned facts, user preferences, project knowledge, skills | Structured knowledge graph + markdown | Permanent, versioned |

Key improvements over both projects:
- **Decay scoring** — memories that haven't been recalled in months get compressed/archived, keeping the active memory set fast
- **Vector search + FTS5** — combine keyword search (Hermes's strength) with semantic similarity search for better recall
- **Memory provenance** — every memory links back to the conversation/task that created it, so you can trace *why* the agent believes something

### 3. Skills: Living Skills with Versioning

**Problem with both:** Skills are static markdown files. They don't track performance, don't version, and don't compose.

**Better approach:**

```yaml
# skill.yaml (not just markdown)
name: deploy-to-vercel
version: 3.2.0
created_from: task/2024-03-15-deploy-fix  # provenance
success_rate: 0.94  # tracked automatically
avg_duration: 45s
dependencies:
  - skill: git-operations@^2.0
  - tool: terminal
triggers:
  - pattern: "deploy * to vercel"
  - pattern: "push to production"
steps:
  - ... (executable steps, not just instructions)
changelog:
  - v3.2.0: Added error recovery for rate limiting
  - v3.1.0: Self-improved after failing on monorepo
```

Key improvements:
- **Performance tracking** — success rate, duration, failure modes tracked automatically
- **Semantic versioning** — skills evolve and you can pin versions
- **Composition** — skills can depend on other skills
- **Auto-retirement** — skills that drop below a success threshold get flagged for review
- **Provenance** — every skill traces back to the task that created it

### 4. Self-Improvement: Continuous Learning Pipeline

**Problem:** Hermes has the right idea but it's a simple loop. No evaluation, no A/B testing, no rollback.

**Better approach:**

```
Task Completed
     │
     ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│ Skill        │────▶│ Sandbox      │────▶│ Promote or   │
│ Extraction   │     │ Evaluation   │     │ Discard      │
└─────────────┘     └──────────────┘     └─────────────┘
                          │
                    Run skill on                    
                    synthetic variants              
                    of the original task            
```

- New skills are **extracted** from successful tasks (like Hermes)
- But then they're **evaluated in a sandbox** against synthetic variations of the original task
- Only skills that pass evaluation get **promoted** to the active skill set
- Skills that cause failures get **automatically rolled back** to the previous version

### 5. Channel System: Plugin-Based with Hot Reload

**Problem:** OpenClaw's adapters are good but compiled into the main process. Adding a new channel requires a restart.

**Better approach:**
- Each channel adapter is an **independent process** that connects to the event bus
- Adapters can be **hot-loaded and hot-swapped** without restarting the agent
- A standard **Channel Protocol** (like LSP but for messaging) means anyone can write an adapter in any language
- Ship official adapters for the top 5 platforms, let the community build the rest

### 6. User Modeling: Explicit + Implicit

**Problem:** Hermes does implicit modeling (Honcho). Neither does explicit modeling.

**Better approach — both layers:**
- **Implicit** (like Hermes): Learn preferences from behavior — what tools the user prefers, their coding style, their timezone patterns
- **Explicit**: Let users directly tell the agent things: "I prefer TypeScript over JavaScript," "Always use pnpm," "My deploy target is Vercel." Stored as structured facts, not buried in conversation history.
- **Queryable**: The user can ask "what do you know about me?" and get a clear, editable answer

### 7. Observability: Built-In from Day One

**Problem with both:** Neither has real observability. You can't trace a request through the system, measure latency, or debug why the agent made a particular decision.

**Better approach:**
- **OpenTelemetry traces** on every agent action (tool call, LLM request, memory lookup)
- **Decision logs** — structured logs explaining *why* the agent chose each action
- **Web dashboard** — real-time view of agent activity, memory state, skill performance
- **Cost tracking** — per-task token usage and API costs

### 8. Eval Framework: Know If You're Getting Better

**Problem with both:** No way to measure agent quality. You're flying blind.

**Better approach:**
- **Benchmark suite** — standard tasks the agent runs periodically to measure regression
- **A/B testing** — test new skills/prompts/models against baselines
- **User feedback loop** — thumbs up/down on agent outputs, feeding back into skill improvement
- **Automated scorecards** — weekly reports on success rate, task completion time, user satisfaction

---

## Recommended Tech Stack (If Starting Fresh)

| Layer | Technology | Why |
|---|---|---|
| **Event Bus** | NATS or Redis Streams | Lightweight, polyglot, battle-tested |
| **Gateway / Channels** | TypeScript / Bun | Best ecosystem for WebSocket + chat platform SDKs |
| **Agent Runtime** | Python | Best ecosystem for ML, LLM libraries, rapid prototyping |
| **Memory Store** | SQLite + vector extension (sqlite-vec) | Single-file database, no external dependencies, FTS5 + vector search |
| **Knowledge Graph** | Markdown files + SQLite index | Human-readable + machine-searchable |
| **Skills Format** | YAML + markdown (executable) | Structured metadata + human-readable instructions |
| **Inter-Service Protocol** | JSON-RPC over NATS (or stdio for simple setups) | Language-agnostic, debuggable |
| **Observability** | OpenTelemetry + Grafana | Industry standard |
| **CLI** | Rust or Go | Fast startup, single binary distribution |
| **Web Dashboard** | SvelteKit or Next.js | Modern, reactive, good DX |

---

## What This Gets You That Neither Project Has

| Capability | OpenClaw | Hermes | **Greenfield** |
|---|---|---|---|
| Hot-swappable services | ❌ | ❌ | ✅ |
| Tiered memory with decay | ❌ | Partial | ✅ |
| Skill versioning + performance tracking | ❌ | ❌ | ✅ |
| Sandboxed skill evaluation | ❌ | ❌ | ✅ |
| Built-in observability | ❌ | ❌ | ✅ |
| Eval/benchmark framework | ❌ | ❌ | ✅ |
| Polyglot service architecture | ❌ | ❌ | ✅ |
| Explicit + implicit user modeling | ❌ | Implicit only | ✅ |
| Web dashboard | ❌ | ❌ | ✅ |
| Skill auto-rollback | ❌ | ❌ | ✅ |

---

## The Trade-Off

**Starting from scratch costs time** — probably 6–12 months to reach feature parity with either project. But you'd leapfrog both in architecture quality, and you wouldn't carry their technical debt.

**The pragmatic middle ground:** Start from scratch on the **core architecture** (event bus, memory system, skill engine, eval framework) but **borrow liberally** from both codebases for the commodity parts (channel adapters from OpenClaw, ReAct loop patterns from Hermes). MIT lets you do exactly that.
