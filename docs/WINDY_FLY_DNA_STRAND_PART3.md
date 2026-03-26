# 🧬 WINDY FLY — DNA STRAND MASTER PLAN

## Part 3 of 4: Phases 3–4 (Skills + Intents + Gateway + Advanced)

---

## 8. PHASE 3 — SKILL ENGINE + COST LEDGER + INTENT SYSTEM (Week 4–8)

> **GOAL:** Agent can create, evaluate, and promote skills. Cost tracking enforces budgets. Intent system tracks user goals with decay. Emotional awareness adjusts behavior based on user stress signals.

### STEP 3.1: Skill Engine

| # | Task | File |
|---|---|---|
| 3.1.1 | Create skill manager | `src/windyfly/skills/manager.py` |
| 3.1.2 | Create skill sandbox | `src/windyfly/skills/sandbox.py` |
| 3.1.3 | Create skill evaluator | `src/windyfly/skills/evaluator.py` |

**`manager.py` spec:**
```python
# Function: create_skill(db, name, code, language, description,
#           permissions_required=None, risk_level="low") -> str
#   INSERT into skills table with promoted=FALSE, version=1
#   Returns skill id
#
# Function: get_skill(db, skill_id) -> dict | None
# Function: get_skill_by_name(db, name) -> dict | None
# Function: list_skills(db, promoted_only=True) -> list[dict]
#
# Function: promote_skill(db, skill_id)
#   UPDATE skills SET promoted=TRUE WHERE id=skill_id
#   Only callable after evaluator passes all gates
#
# Function: increment_usage(db, write_queue, skill_id, success: bool)
#   Enqueue UPDATE: usage_count += 1, success_count or failure_count += 1
#   Priority: MEDIUM
#
# Function: rollback_skill(db, skill_id) -> str | None
#   Find parent_skill_id, set current version promoted=FALSE
#   Set parent version promoted=TRUE
#   Returns parent skill id or None if no parent
```

**`sandbox.py` spec:**
```python
# Function: execute_in_sandbox(code: str, language: str,
#           test_input: str = None, timeout: int = 10) -> dict
#   v1: Use subprocess with timeout and restricted environment
#     - Python: subprocess.run(["python", "-c", code], timeout=timeout,
#       env={"PATH": "/usr/bin"}, cwd="/tmp")
#     - Capture stdout, stderr, return code
#   v2 (future): Docker container execution
#   Returns: { "success": bool, "stdout": str, "stderr": str,
#              "exit_code": int, "timed_out": bool }
```

**`evaluator.py` spec:**
```python
# Function: evaluate_skill(db, skill_id) -> dict
#   Gate 1 — Syntax: attempt to parse/compile the code
#     Python: compile(code, "<skill>", "exec")
#     If fails: return { "passed": False, "gate": "syntax", "error": str }
#
#   Gate 2 — Execution: run in sandbox with test input (if provided)
#     Call execute_in_sandbox(). If exit_code != 0: fail
#
#   Gate 3 — Safety: scan for dangerous patterns
#     BANNED_PATTERNS = [
#       r"import\s+os", r"import\s+subprocess", r"import\s+shutil",
#       r"open\s*\(", r"exec\s*\(", r"eval\s*\(", r"__import__",
#       r"rm\s+-rf", r"curl\s+", r"wget\s+"
#     ]
#     If any match and not in permissions_required: fail
#
#   Return: { "passed": bool, "gates": { "syntax": bool, "execution": bool,
#             "safety": bool }, "details": str }
```

### STEP 3.2: Cost Ledger Enforcement

| # | Task | File |
|---|---|---|
| 3.2.1 | Create cost tracker | `src/windyfly/memory/cost_tracker.py` |

**`cost_tracker.py` spec:**
```python
# Function: log_cost(db, write_queue, model, input_tokens, output_tokens,
#           cost_usd, task_type="chat")
#   Enqueue INSERT into cost_ledger. Priority: MEDIUM.
#
# Function: get_daily_spend(db) -> float
#   SELECT SUM(cost_usd) FROM cost_ledger
#   WHERE created_at >= date('now', 'start of day')
#
# Function: get_monthly_spend(db) -> float
#   WHERE created_at >= date('now', 'start of month')
#
# Function: check_budget(db, config, proposed_cost: float) -> dict
#   daily = get_daily_spend(db) + proposed_cost
#   Returns: {
#     "allowed": daily <= config["costs"]["daily_budget_usd"],
#     "daily_spend": daily,
#     "daily_budget": config budget,
#     "warning": daily > config["costs"]["warn_at_usd"]
#   }
#   If not allowed: agent must ask user before proceeding
#
# COST_PER_1K_TOKENS (dict — update as prices change):
COST_MAP = {
    "gpt-4o-mini":    {"input": 0.00015, "output": 0.0006},
    "gpt-4o":         {"input": 0.0025,  "output": 0.01},
    "claude-sonnet":  {"input": 0.003,   "output": 0.015},
    "claude-haiku":   {"input": 0.00025, "output": 0.00125},
}
#
# Function: estimate_cost(model, input_tokens, output_tokens) -> float
#   Uses COST_MAP to calculate USD cost
```

**Integration into `loop.py`:**
- Before calling LLM: `check_budget()`. If not allowed, respond: "I've hit my daily budget ($X.XX of $Y.YY). Want me to proceed anyway?"
- After LLM call: `log_cost()` via write queue

### STEP 3.3: Intent System (Phase 2 Schema — Add via Migration)

| # | Task | File |
|---|---|---|
| 3.3.1 | Create migration v2 | `src/windyfly/memory/database.py` — add migration |
| 3.3.2 | Create intents CRUD | `src/windyfly/memory/intents.py` |
| 3.3.3 | Create intent detector | `src/windyfly/agent/intent_detector.py` |

**Migration v2 SQL:**
```sql
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    scope_id TEXT DEFAULT 'personal',
    description TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    priority INTEGER DEFAULT 5,
    origin TEXT DEFAULT 'user_said',
    autonomy_policy TEXT DEFAULT 'inform',
    decay_score REAL DEFAULT 1.0,
    linked_nodes JSON,
    last_touched DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES nodes(id),
    target_id TEXT NOT NULL REFERENCES nodes(id),
    relation TEXT NOT NULL,
    strength REAL DEFAULT 1.0,
    confidence REAL DEFAULT 1.0,
    timestamp_weight REAL DEFAULT 1.0,
    source_weight REAL DEFAULT 1.0,
    decay_score REAL DEFAULT 1.0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conflicts (
    id TEXT PRIMARY KEY,
    node_id TEXT REFERENCES nodes(id),
    old_value TEXT, new_value TEXT,
    resolution_status TEXT DEFAULT 'unresolved',
    user_resolved BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME
);

CREATE TABLE IF NOT EXISTS soul_history (
    id TEXT PRIMARY KEY,
    soul_id TEXT NOT NULL REFERENCES soul(id),
    old_value TEXT, new_value TEXT,
    changed_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version, description)
    VALUES (2, 'Phase 3: intents, edges, conflicts, soul_history');
```

**`intent_detector.py` spec:**
```python
# Function: detect_intent(user_message: str, context: list[dict]) -> dict | None
#   Uses LLM to analyze: "Does this message express a goal or intent?"
#   Prompt: "Given this message, extract any goals/intents the user is expressing.
#            Respond with JSON: { 'has_intent': bool, 'description': str,
#            'origin': 'user_said' | 'inferred_from_chat' }"
#   If has_intent: return parsed dict
#   If not: return None
#
# Function: surface_pending_intents(db, user_id="default") -> list[dict]
#   SELECT * FROM intents WHERE status='active' AND origin='inferred_from_chat'
#   AND created_at > datetime('now', '-24 hours')
#   These are inferred intents that haven't been confirmed yet → Intent Inbox
#
# Function: decay_intents(db, write_queue)
#   UPDATE intents SET decay_score = decay_score * 0.95
#   WHERE status='active' AND last_touched < datetime('now', '-7 days')
#   Intents with decay_score < 0.3: set status='paused'
#   Priority: LOW
```

### STEP 3.4: Emotional Awareness

| # | Task | File |
|---|---|---|
| 3.4.1 | Create emotion detector | `src/windyfly/agent/emotion_detector.py` |

**`emotion_detector.py` spec:**
```python
# EMOTION_SIGNALS (pattern-based, no LLM needed):
STRESS_SIGNALS = [
    r"(?i)(ugh|frustrated|annoying|this is (broken|stupid)|wtf|ffs)",
    r"(?i)(i('m| am) (stressed|tired|exhausted|overwhelmed))",
    r"[A-Z]{5,}",  # ALL CAPS (shouting)
    r"(!{3,})",     # Multiple exclamation marks
]
EXCITEMENT_SIGNALS = [
    r"(?i)(awesome|amazing|perfect|love it|yes!|great|wow)",
    r"(?i)(this is (great|incredible|perfect))",
]
#
# Function: detect_emotional_context(message: str) -> str
#   Check STRESS_SIGNALS → return "stressed"
#   Check EXCITEMENT_SIGNALS → return "excited"
#   Else → return "neutral"
#
# Function: get_emotional_trend(db, session_id, window=5) -> str
#   Get last N episodes for this session
#   Count emotional_context values
#   If 3+ consecutive "stressed": return "sustained_stress"
#   Else return majority emotion or "neutral"
```

**Integration into `prompt.py`:**
- On `sustained_stress`: add system instruction: "The user seems stressed. Be extra concise and supportive. Don't suggest new things right now. Focus on what they're asking."
- On `excited`: maintain energy, match enthusiasm

### STEP 3.5: Tests + Commit

| # | Task |
|---|---|
| 3.5.1 | Write `tests/test_skills.py` — test create, evaluate, promote, rollback |
| 3.5.2 | Write `tests/test_cost_tracker.py` — test logging, budget check, estimation |
| 3.5.3 | Write `tests/test_intents.py` — test CRUD, decay, surfacing |
| 3.5.4 | Write `tests/test_emotion.py` — test pattern detection, trend analysis |
| 3.5.5 | Run all tests: `uv run pytest tests/ -v` |
| 3.5.6 | Commit + tag v0.4.0 |

**🎯 PHASE 3 COMPLETE CRITERIA:**
- [ ] Skills can be created, sandboxed, evaluated (3 gates), promoted, rolled back
- [ ] Cost tracking logs every LLM call, enforces daily budget, warns at threshold
- [ ] Intents detected from conversation, surfaced in Intent Inbox within 24h
- [ ] Intent decay runs, stale goals auto-downrank
- [ ] Emotional context detected per message, trend tracked per session
- [ ] Agent adjusts behavior on sustained stress
- [ ] Migration v2 adds 4 new tables
- [ ] All tests pass, tagged v0.4.0

---

## 9. PHASE 4 — BUN GATEWAY + ADVANCED FEATURES (Week 8–14)

> **GOAL:** TypeScript Gateway for web UI, cognitive decay, conflict resolution, sub-agent orchestration, offline mode.

### STEP 4.1: Bun Gateway

| # | Task | File |
|---|---|---|
| 4.1.1 | Create gateway directory | `gateway/` at repo root |
| 4.1.2 | Init Bun project | `cd gateway && bun init` |
| 4.1.3 | Create UDS server in Brain | `src/windyfly/bridge/uds_server.py` |
| 4.1.4 | Create UDS client in Gateway | `gateway/src/bridge.ts` |
| 4.1.5 | Create web server | `gateway/src/server.ts` |
| 4.1.6 | Create WebSocket handler | `gateway/src/websocket.ts` |

**`uds_server.py` spec:**
```python
# Class: UDSBridge
#   Socket path: /tmp/windyfly.sock
#   Protocol: JSON over Unix Domain Socket
#   Each message: {"id": uuid, "method": str, "params": dict}
#   Each response: {"id": uuid, "result": any, "error": str | null}
#
# Methods exposed:
#   "agent.respond" → calls agent_respond(), returns response text
#   "memory.search" → calls search_nodes() + search_episodes()
#   "sliders.get" → calls get_sliders()
#   "sliders.set" → calls set_slider()
#   "cost.daily" → calls get_daily_spend()
#   "intents.list" → calls surface_pending_intents()
```

**`gateway/src/server.ts` spec:**
- Bun HTTP server on port 3000
- Routes:
  - `GET /api/health` → `{ status: "ok" }`
  - `GET /api/sliders` → proxy to UDS `sliders.get`
  - `PUT /api/sliders/:name` → proxy to UDS `sliders.set`
  - `GET /api/cost/daily` → proxy to UDS `cost.daily`
  - `GET /api/intents` → proxy to UDS `intents.list`
  - `WS /ws/chat` → WebSocket for real-time chat (proxies to `agent.respond`)

### STEP 4.2: Cognitive Decay

| # | Task | File |
|---|---|---|
| 4.2.1 | Create decay engine | `src/windyfly/memory/decay.py` |

**`decay.py` spec:**
```python
# Function: run_decay(db, write_queue)
#   Background task, runs every 24 hours (or on demand)
#
#   1. Nodes: UPDATE nodes SET decay_score = decay_score * 0.98
#      WHERE updated_at < datetime('now', '-30 days')
#   2. Low-decay nodes (decay_score < 0.2): mark epistemic_status='speculative'
#   3. Very low nodes (decay_score < 0.05): DELETE (permanent forget)
#   4. Episodes: older than 90 days without summary → generate summary via LLM,
#      then delete raw content (keep summary only)
#   5. Intents: handled in intent_detector.decay_intents()
#   Priority: ALL writes are LOW
```

### STEP 4.3: Conflict Resolution

| # | Task | File |
|---|---|---|
| 4.3.1 | Create conflict detector | `src/windyfly/memory/conflict_detector.py` |

**`conflict_detector.py` spec:**
```python
# Function: check_for_conflict(db, node_type, node_name, new_value) -> dict | None
#   Find existing node with same (type, name)
#   If exists and value differs:
#     INSERT into conflicts table
#     Return { "conflict_id": str, "old_value": str, "new_value": str }
#   If no conflict: return None
#
# Function: resolve_conflict(db, conflict_id, resolution: str, keep_new: bool)
#   UPDATE conflicts SET resolution_status='user_resolved', resolved_at=now
#   If keep_new: update the node with new value
#   If not: keep old value, discard new
```

**Integration:** Before any node upsert, call `check_for_conflict()`. If conflict found, agent asks: "You previously said [old]. Now it seems [new]. Should I update?"

### STEP 4.4: Sub-Agent Orchestration (v1 — Pseudo)

| # | Task | File |
|---|---|---|
| 4.4.1 | Create sub-agent runner | `src/windyfly/agent/sub_agents.py` |

**`sub_agents.py` spec:**
```python
# Function: spawn_sub_agent(config, db, write_queue, task: str,
#           token_budget: int = 2000, timeout: int = 30) -> str
#   1. Create isolated context (no access to parent conversation)
#   2. System prompt: "You are a specialist sub-agent. Your task: {task}.
#      Respond with your findings only. Budget: {token_budget} tokens."
#   3. Call call_llm() with isolated messages
#   4. Log cost separately with task_type="sub_agent"
#   5. Return result text
#   6. Depth limit: 1 (sub-agents cannot spawn sub-agents)
```

### STEP 4.5: Offline Mode

| # | Task | File |
|---|---|---|
| 4.5.1 | Create offline fallback | `src/windyfly/agent/offline.py` |

**`offline.py` spec:**
```python
# Function: is_online() -> bool
#   Try httpx.get("https://api.openai.com", timeout=3)
#   Return True if status 200-499, False on timeout/connection error
#
# Function: get_offline_response(user_message, context) -> str
#   If Ollama is running locally (check localhost:11434):
#     Call Ollama API with local model
#   Else:
#     Return "I'm currently offline and don't have a local model available.
#     I'll process your message when connectivity returns."
#   Queue the message for processing when online
```

### STEP 4.6: Tests + Commit

| # | Task |
|---|---|
| 4.6.1 | Test UDS bridge (mock socket) |
| 4.6.2 | Test decay engine (verify score decrements, deletions) |
| 4.6.3 | Test conflict detector (verify detection, resolution) |
| 4.6.4 | Test sub-agent (verify isolation, budget enforcement) |
| 4.6.5 | Test offline fallback |
| 4.6.6 | Run all: `uv run pytest tests/ -v` |
| 4.6.7 | Commit + tag v0.5.0 |

**🎯 PHASE 4 COMPLETE CRITERIA:**
- [ ] Bun gateway serves web UI on port 3000
- [ ] WebSocket chat works through gateway → UDS → Brain
- [ ] Cognitive decay runs, old nodes degrade, ancient content pruned
- [ ] Conflicts detected and surfaced to user before silent overwrite
- [ ] Sub-agents execute isolated tasks within token/time budgets
- [ ] Offline mode returns local model response or queues for later
- [ ] All tests pass, tagged v0.5.0

---

> **CONTINUES IN PART 4:** Phase 5 (Dashboard + Observability), Ecosystem Integration Map, Appendices
