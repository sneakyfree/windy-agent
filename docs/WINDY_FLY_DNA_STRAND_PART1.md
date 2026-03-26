# 🧬 WINDY FLY — DNA STRAND MASTER PLAN

## Part 1 of 4: Foundation + Phase 0

**Codename:** Windy Fly
**What it is:** The AI agent brain of the Windy ecosystem — a lifelong, self-improving, user-sovereign companion.
**Repo:** `windy-agent/` (standalone repo, connects to Windy ecosystem via Matrix + REST API)
**Created:** 25 March 2026
**Predecessors:** Synthesized architecture from Grok, Gemini, ChatGPT, Perplexity, Perplexity CM, Antigravity (Opus 4.6)

> **DNA STRAND RULE:** Every task below is atomic. One task = one action. A 1M-parameter model reads one task, executes it, verifies it, moves to the next. No ambiguity. No judgment calls. If a task requires a decision, the decision is already made here.

---

## 1. PRODUCT IDENTITY

| Field | Value |
|---|---|
| **Name** | Windy Fly |
| **Tagline** | "Your AI. Your Rules. Your Ecosystem." |
| **Matrix Bot ID** | `@windyfly:chat.windypro.com` |
| **Ecosystem** | Windy Pro (desktop), Windy Pro Mobile, Windy Chat (Matrix/Synapse), Windy Cloud |
| **License** | Proprietary (same as Windy Pro) |
| **Primary Language** | Python 3.12+ (Brain) |
| **Secondary Language** | TypeScript/Bun (Gateway — Phase 1+) |
| **Database** | SQLite + sqlite-vec + FTS5 |
| **Chat Protocol** | Matrix (via matrix-nio for Python) |
| **Config Format** | TOML |
| **Package Manager** | uv (Python), bun (TypeScript) |

---

## 2. ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────┐
│                      WINDY FLY AGENT                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  BRAIN (Python 3.12+ / uv)                              │   │
│  │                                                          │   │
│  │  ┌────────────┐  ┌───────────┐  ┌──────────────────┐   │   │
│  │  │ Agent Loop │  │ Memory    │  │ Personality      │   │   │
│  │  │ (ReAct)    │  │ Manager   │  │ Engine (SOUL.md) │   │   │
│  │  └─────┬──────┘  └─────┬─────┘  └────────┬─────────┘   │   │
│  │        │               │                  │              │   │
│  │  ┌─────┴───────────────┴──────────────────┴─────────┐   │   │
│  │  │              WRITE QUEUE (in-process)             │   │   │
│  │  └──────────────────────┬───────────────────────────┘   │   │
│  │                         │                                │   │
│  │  ┌──────────────────────┴───────────────────────────┐   │   │
│  │  │  SQLite (windyfly.db) + sqlite-vec + FTS5        │   │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                         │                                       │
│            ┌────────────┴────────────┐                          │
│            │    Matrix (matrix-nio)  │                          │
│            │    Bot: @windyfly       │                          │
│            └────────────┬────────────┘                          │
└─────────────────────────┼───────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   Windy Chat        Windy Pro       Windy Pro
   (Synapse)         Desktop API     Mobile API
   Port 8008         Port 8098       Port 8098
```

---

## 3. REPO STRUCTURE (Create in Phase 0)

```
windy-agent/
├── pyproject.toml              # uv project config
├── windyfly.toml               # Agent runtime config
├── SOUL.md                     # Personality definition
├── README.md                   # Project readme
├── .gitignore
├── .env.example                # Environment variables template
├── src/
│   └── windyfly/
│       ├── __init__.py
│       ├── main.py             # Entry point
│       ├── config.py           # TOML config loader
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── loop.py         # Core ReAct agent loop
│       │   ├── prompt.py       # Prompt assembly (personality + memory + tools)
│       │   └── models.py       # LLM provider abstraction
│       ├── memory/
│       │   ├── __init__.py
│       │   ├── database.py     # SQLite connection + migrations
│       │   ├── nodes.py        # Knowledge graph CRUD
│       │   ├── episodes.py     # Conversation history CRUD
│       │   ├── soul.py         # Soul/personality CRUD
│       │   ├── skills.py       # Skills CRUD
│       │   ├── failures.py     # "Never Wrong Twice" CRUD
│       │   ├── cost_ledger.py  # Cost tracking CRUD
│       │   └── write_queue.py  # Priority write queue
│       ├── personality/
│       │   ├── __init__.py
│       │   ├── engine.py       # SOUL.md parser + personality injection
│       │   └── mode.py         # Mode switching (companion/focused/neutral)
│       ├── channels/
│       │   ├── __init__.py
│       │   ├── matrix_bot.py   # Matrix/Synapse bot (matrix-nio)
│       │   └── cli.py          # CLI interface (dev/testing)
│       └── tools/
│           ├── __init__.py
│           ├── registry.py     # Tool registration + dispatch
│           ├── windy_api.py    # Windy Pro account-server tools
│           └── web_search.py   # Web search tool
├── tests/
│   ├── __init__.py
│   ├── test_agent_loop.py
│   ├── test_memory.py
│   ├── test_personality.py
│   └── test_matrix_bot.py
└── data/
    └── .gitkeep                # windyfly.db created here at runtime
```

---

## 4. DATABASE SCHEMA (Phase 0 — 6 Tables)

> **CODON RULE:** Execute these SQL statements EXACTLY. Do not modify column names, types, or defaults. Every table is phase-tagged so ribosomes know when to create it.

```sql
-- ═══════════════════════════════════════════════
-- PRAGMA SETTINGS (run on every connection open)
-- ═══════════════════════════════════════════════
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

-- ═══════════════════════════════════════════════
-- TABLE 1/6: nodes (Phase 0)
-- Purpose: Entities in the user's life-graph
-- ═══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    scope_id TEXT DEFAULT 'personal',
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    metadata JSON,
    epistemic_status TEXT DEFAULT 'inferred',
    confidence REAL DEFAULT 1.0,
    source TEXT DEFAULT 'inferred',
    verification_method TEXT,
    last_verified_at DATETIME,
    valid_from TEXT,
    valid_until TEXT,
    decay_score REAL DEFAULT 1.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════
-- TABLE 2/6: episodes (Phase 0)
-- Purpose: Conversation and event history
-- ═══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    token_count INTEGER,
    cost_usd REAL,
    emotional_context TEXT,
    embedding BLOB,
    embedding_model TEXT,
    embedding_version INTEGER DEFAULT 1,
    last_accessed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════
-- TABLE 3/6: soul (Phase 0)
-- Purpose: Personality, identity, control panel sliders
-- ═══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS soul (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    source TEXT DEFAULT 'default',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════
-- TABLE 4/6: skills (Phase 0)
-- Purpose: Versioned, self-improving code
-- ═══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS skills (
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

-- ═══════════════════════════════════════════════
-- TABLE 5/6: failures (Phase 0)
-- Purpose: "Never Wrong Twice" learning system
-- ═══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS failures (
    id TEXT PRIMARY KEY,
    fault_type TEXT NOT NULL,
    description TEXT NOT NULL,
    root_cause TEXT,
    correction_action TEXT,
    correction_skill_id TEXT,
    improvement_verified BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME
);

-- ═══════════════════════════════════════════════
-- TABLE 6/6: cost_ledger (Phase 0)
-- Purpose: API spend tracking
-- ═══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS cost_ledger (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    task_type TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════
-- FTS INDEX (Phase 0)
-- ═══════════════════════════════════════════════
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
    USING fts5(content, summary, content='episodes', content_rowid='rowid');

-- ═══════════════════════════════════════════════
-- SCHEMA VERSION TRACKER
-- ═══════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);
INSERT OR IGNORE INTO schema_version (version, description)
    VALUES (1, 'Phase 0: 6 core tables + FTS');
```

---

## 5. PHASE 0 — PYTHON AGENT LOOP (Week 0–1)

> **GOAL:** A working Python agent that can hold a conversation, remember things, and respond with personality. CLI only. No gateway. No Matrix. No tools. Day 5 demo: you type, it responds, it remembers.

### PHASE 0 — STEP 0.1: Initialize Repository

| # | Task | Exact Command / Action | Verify |
|---|---|---|---|
| 0.1.1 | Create repo directory | `mkdir -p ~/windy-agent && cd ~/windy-agent && git init` | `ls -la ~/windy-agent/.git` returns directory listing |
| 0.1.2 | Create `.gitignore` | Write file with contents: `__pycache__/`, `*.pyc`, `.env`, `data/*.db`, `data/*.db-wal`, `data/*.db-shm`, `.venv/`, `node_modules/`, `dist/`, `.ruff_cache/` | `cat ~/windy-agent/.gitignore` shows contents |
| 0.1.3 | Create `.env.example` | Write file with: `OPENAI_API_KEY=sk-xxx`, `ANTHROPIC_API_KEY=sk-ant-xxx`, `DEFAULT_MODEL=gpt-4o-mini`, `WINDYFLY_DB_PATH=data/windyfly.db`, `LOG_LEVEL=INFO`, `MATRIX_HOMESERVER=https://chat.windypro.com`, `MATRIX_BOT_USER=@windyfly:chat.windypro.com`, `MATRIX_BOT_TOKEN=` | File exists with all keys |
| 0.1.4 | Initialize uv project | `cd ~/windy-agent && uv init --name windyfly --python 3.12` | `pyproject.toml` exists |
| 0.1.5 | Edit `pyproject.toml` | Set `requires-python = ">=3.12"`. Add dependencies: `openai>=1.0`, `anthropic>=0.40`, `httpx>=0.27`, `pydantic>=2.0`, `tomli>=2.0` (if py<3.11), `rich>=13.0` (for CLI), `matrix-nio>=0.24` (for Matrix), `sqlite-vec>=0.1` | `uv sync` succeeds |
| 0.1.6 | Create directory structure | `mkdir -p src/windyfly/{agent,memory,personality,channels,tools} tests data` | All directories exist |
| 0.1.7 | Create all `__init__.py` files | `touch src/windyfly/__init__.py src/windyfly/agent/__init__.py src/windyfly/memory/__init__.py src/windyfly/personality/__init__.py src/windyfly/channels/__init__.py src/windyfly/tools/__init__.py tests/__init__.py` | All files exist |
| 0.1.8 | Create `data/.gitkeep` | `touch data/.gitkeep` | File exists |
| 0.1.9 | Initial commit | `git add -A && git commit -m "feat: initialize windy-agent repo structure"` | `git log --oneline` shows commit |

### PHASE 0 — STEP 0.2: Config System

| # | Task | File | What to Write |
|---|---|---|---|
| 0.2.1 | Create TOML config | `windyfly.toml` | See spec below |
| 0.2.2 | Create config loader | `src/windyfly/config.py` | See spec below |

**`windyfly.toml` exact contents:**
```toml
[agent]
name = "Windy Fly"
default_model = "gpt-4o-mini"
max_context_tokens = 8000
max_response_tokens = 2000
temperature = 0.7

[memory]
db_path = "data/windyfly.db"
max_episodes_per_context = 20
max_nodes_per_context = 10

[personality]
soul_path = "SOUL.md"
humor_level = 7
formality = 4
proactivity = 5
verbosity = 5
reasoning_depth = 6
autonomy = 3
epistemic_strictness = 5

[costs]
daily_budget_usd = 5.0
warn_at_usd = 0.50

[matrix]
homeserver = "https://chat.windypro.com"
bot_user = "@windyfly:chat.windypro.com"
```

**`src/windyfly/config.py` spec:**
- Function `load_config(path: str = "windyfly.toml") -> dict` that reads TOML file
- Falls back to env vars for secrets (OPENAI_API_KEY, etc.)
- Returns a dict with all config values, merged with env overrides
- Uses `tomllib` (stdlib in 3.11+) or `tomli` for older Python
- Verify: `python -c "from windyfly.config import load_config; print(load_config())"` prints config dict

### PHASE 0 — STEP 0.3: Database Layer

| # | Task | File | What to Write |
|---|---|---|---|
| 0.3.1 | Create database module | `src/windyfly/memory/database.py` | See spec below |
| 0.3.2 | Create episodes CRUD | `src/windyfly/memory/episodes.py` | See spec below |
| 0.3.3 | Create nodes CRUD | `src/windyfly/memory/nodes.py` | See spec below |
| 0.3.4 | Create soul CRUD | `src/windyfly/memory/soul.py` | See spec below |
| 0.3.5 | Create cost_ledger CRUD | `src/windyfly/memory/cost_ledger.py` | See spec below |
| 0.3.6 | Create failures CRUD | `src/windyfly/memory/failures.py` | See spec below |
| 0.3.7 | Create write queue | `src/windyfly/memory/write_queue.py` | See spec below |

**`database.py` spec:**
```python
# Class: Database
# __init__(self, db_path: str)
#   - Creates data/ directory if not exists
#   - Opens sqlite3 connection with: detect_types=sqlite3.PARSE_DECLTYPES
#   - Runs all PRAGMA statements from Section 4 above
#   - Calls self._run_migrations()
#
# _run_migrations(self)
#   - Reads current schema_version from DB (0 if table doesn't exist)
#   - Applies each migration in order (v1 = Phase 0 schema from Section 4)
#   - Each migration is a string of SQL executed in a transaction
#
# execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor
# executemany(self, sql: str, params_list: list) -> sqlite3.Cursor
# fetchone(self, sql: str, params: tuple = ()) -> dict | None
# fetchall(self, sql: str, params: tuple = ()) -> list[dict]
# close(self)
#
# Row factory: sqlite3.Row (so results are dict-like)
```

**`episodes.py` spec:**
```python
# Function: save_episode(db: Database, role: str, content: str,
#           session_id: str = None, summary: str = None,
#           token_count: int = None, cost_usd: float = None,
#           emotional_context: str = None) -> str
#   - Generates UUID4 as id
#   - INSERTs into episodes table
#   - Returns the id
#
# Function: get_recent_episodes(db: Database, limit: int = 20,
#           session_id: str = None) -> list[dict]
#   - SELECTs from episodes ORDER BY created_at DESC LIMIT limit
#   - If session_id provided, filters by it
#   - Returns list of dicts
#
# Function: search_episodes(db: Database, query: str, limit: int = 10) -> list[dict]
#   - Uses episodes_fts: SELECT * FROM episodes WHERE rowid IN
#     (SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ?)
#   - Returns list of dicts
```

**`nodes.py` spec:**
```python
# Function: upsert_node(db, type, name, metadata=None, epistemic_status='inferred',
#           confidence=1.0, source='inferred', scope_id='personal',
#           valid_from=None, valid_until=None) -> str
#   - If node with same (type, name, scope_id) exists: UPDATE it
#   - Else: INSERT with UUID4 id
#   - Returns id
#
# Function: get_nodes_by_type(db, type, limit=10) -> list[dict]
# Function: search_nodes(db, query: str, limit=10) -> list[dict]
#   - LIKE search on name and JSON_EXTRACT(metadata, '$')
# Function: get_node(db, node_id: str) -> dict | None
```

**`write_queue.py` spec:**
```python
# Class: WriteQueue
#   Uses threading.Thread as daemon
#   Three priority levels: HIGH=0, MEDIUM=1, LOW=2
#   Internal queue: queue.PriorityQueue
#
# enqueue(self, priority: int, fn: Callable, *args, **kwargs)
#   - Puts (priority, counter, fn, args, kwargs) on queue
#
# _worker(self)
#   - Runs in daemon thread
#   - Pulls from queue, calls fn(*args, **kwargs)
#   - Wraps in try/except, logs errors
#   - MEDIUM items batched: collect up to 50, execute in one transaction
#
# start(self) - starts daemon thread
# stop(self) - signals thread to stop, joins with timeout=5s
```

**Verify Step 0.3:** `python -c "from windyfly.memory.database import Database; db = Database('data/test.db'); print('OK'); db.close()"` → prints "OK" and creates `data/test.db` with all 6 tables. Then delete test.db.

### PHASE 0 — STEP 0.4: Personality Engine

| # | Task | File |
|---|---|---|
| 0.4.1 | Create `SOUL.md` | `SOUL.md` (repo root) |
| 0.4.2 | Create personality engine | `src/windyfly/personality/engine.py` |

**`SOUL.md` exact contents:**
```markdown
# Windy Fly — Soul Definition

## Core Identity
You are Windy Fly, a personal AI companion built by the Windy team. You are part of the Windy ecosystem — voice-to-text, translation, chat, storage, and now intelligent assistance.

## Personality Traits
- Warm, approachable, and genuinely helpful
- Witty but never sarcastic at the user's expense
- Confident in what you know, honest about what you don't
- Proactive — you anticipate needs, but always ask before acting on big things
- You remember everything the user tells you and reference it naturally

## Communication Style
- Clear and concise by default, detailed when asked
- Use casual language, not corporate speak
- Mirror the user's energy — if they're brief, be brief; if they elaborate, elaborate
- Never use phrases like "I'm just an AI" or "As a language model"

## Values
- User sovereignty above all — their data, their rules, their agent
- Truth over comfort — say "I don't know" rather than guess
- Privacy by default — never share or reference data across scopes without permission
- Reliability — if you say you'll do something, do it or explain why you couldn't
```

**`engine.py` spec:**
```python
# Function: load_soul(path: str = "SOUL.md") -> str
#   - Reads SOUL.md file, returns contents as string
#   - If file not found, returns a minimal default personality string
#
# Function: build_personality_block(soul_text: str, sliders: dict) -> str
#   - Takes raw SOUL.md text and slider values from config
#   - If humor_level < 3: strips humor-related lines
#   - If formality > 7: adds "Be formal and professional" instruction
#   - Returns final personality system prompt block (max 600 tokens)
#
# Function: get_mode_override(mode: str) -> str | None
#   - mode = "companion" → returns None (use full personality)
#   - mode = "focused" → returns "You are in focused mode. Be precise and concise."
#   - mode = "neutral" → returns "You are in neutral mode. No humor, no personality flair."
```

### PHASE 0 — STEP 0.5: LLM Provider Abstraction

| # | Task | File |
|---|---|---|
| 0.5.1 | Create models module | `src/windyfly/agent/models.py` |

**`models.py` spec:**
```python
# Function: call_llm(messages: list[dict], model: str = None,
#           temperature: float = 0.7, max_tokens: int = 2000,
#           tools: list[dict] = None) -> dict
#   - If model starts with "gpt" or "o": use openai.ChatCompletion
#   - If model starts with "claude": use anthropic.messages.create
#   - Returns dict: { "content": str, "model": str,
#     "input_tokens": int, "output_tokens": int, "tool_calls": list | None }
#   - Handles rate limits with 3 retries + exponential backoff
#   - Logs to cost_ledger via write queue after each call
```

### PHASE 0 — STEP 0.6: Core Agent Loop

| # | Task | File |
|---|---|---|
| 0.6.1 | Create prompt assembly | `src/windyfly/agent/prompt.py` |
| 0.6.2 | Create agent loop | `src/windyfly/agent/loop.py` |

**`prompt.py` spec:**
```python
# Function: assemble_prompt(config: dict, db: Database,
#           user_message: str, session_id: str) -> list[dict]
#   Returns a list of message dicts for the LLM:
#   1. System message: personality_block + mode_override (if any)
#   2. Memory context: last N episodes from this session
#   3. Relevant nodes: search nodes matching user_message keywords
#   4. User message: the current input
#   Total must stay under config["agent"]["max_context_tokens"]
```

**`loop.py` spec:**
```python
# Function: agent_respond(config: dict, db: Database, write_queue: WriteQueue,
#           user_message: str, session_id: str) -> str
#   1. Call assemble_prompt() to build messages
#   2. Call call_llm() with assembled messages
#   3. Enqueue episode saves (user message + agent response) via write_queue (HIGH priority)
#   4. Enqueue cost_ledger entry via write_queue (MEDIUM priority)
#   5. Extract any facts from the response and enqueue node upserts (MEDIUM priority)
#   6. Return the agent's response text
```

### PHASE 0 — STEP 0.7: CLI Interface

| # | Task | File |
|---|---|---|
| 0.7.1 | Create CLI channel | `src/windyfly/channels/cli.py` |
| 0.7.2 | Create main entry point | `src/windyfly/main.py` |

**`cli.py` spec:**
```python
# Function: run_cli(config: dict)
#   - Prints "🪰 Windy Fly is ready. Type 'quit' to exit."
#   - Creates Database instance, starts WriteQueue
#   - Generates a session_id (UUID4) for this CLI session
#   - Loop: input("You: ") → agent_respond() → print(f"Fly: {response}")
#   - On 'quit': stop WriteQueue, close Database, exit
#   - Uses rich.console for colored output
```

**`main.py` spec:**
```python
# if __name__ == "__main__":
#   1. Load config via load_config()
#   2. Load .env file (dotenv or manual os.environ read)
#   3. Call run_cli(config)
```

**Verify Step 0.7:** `cd ~/windy-agent && uv run python -m windyfly.main` → prints welcome message, accepts input, responds with personality, remembers context within session.

### PHASE 0 — STEP 0.8: Tests

| # | Task | File |
|---|---|---|
| 0.8.1 | Test database creation | `tests/test_memory.py` |
| 0.8.2 | Test episode CRUD | `tests/test_memory.py` |
| 0.8.3 | Test node CRUD | `tests/test_memory.py` |
| 0.8.4 | Test personality loading | `tests/test_personality.py` |
| 0.8.5 | Test agent loop (mocked LLM) | `tests/test_agent_loop.py` |

**Test spec:** Each test creates a fresh in-memory SQLite database (`:memory:`), runs operations, asserts results. LLM tests mock the `call_llm` function to return predetermined responses.

**Verify:** `cd ~/windy-agent && uv run pytest tests/ -v` → all tests pass.

### PHASE 0 — STEP 0.9: Commit and Tag

| # | Task | Command |
|---|---|---|
| 0.9.1 | Stage all files | `git add -A` |
| 0.9.2 | Commit | `git commit -m "feat: Phase 0 complete — Python agent loop with memory, personality, CLI"` |
| 0.9.3 | Tag | `git tag v0.1.0 -m "Phase 0: Working agent loop"` |

**🎯 PHASE 0 COMPLETE CRITERIA:**
- [ ] `uv run python -m windyfly.main` starts CLI
- [ ] User types message → agent responds with personality
- [ ] Agent remembers previous messages in session
- [ ] Episodes saved to SQLite
- [ ] Cost logged to cost_ledger
- [ ] All tests pass
- [ ] Git tagged v0.1.0

---

> **CONTINUES IN PART 2:** Phase 1 (Matrix Bot + Windy Chat Integration), Phase 2 (Soul Continuity + Truth Layer)
