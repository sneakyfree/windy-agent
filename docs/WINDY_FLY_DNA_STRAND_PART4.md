# 🧬 WINDY FLY — DNA STRAND MASTER PLAN

## Part 4 of 4: Phase 5 + Ecosystem Map + Master Index

---

## 10. PHASE 5 — DASHBOARD + OBSERVABILITY + POLISH (Week 14–18)

> **GOAL:** Full transparency layer. User sees what agent knows, what it costs, and what it got wrong. Personality versioning with drift detection. Automated skill evaluation.

### STEP 5.1: Trust Dashboard Data Endpoints

| # | Task | File |
|---|---|---|
| 5.1.1 | Dashboard data module | `src/windyfly/dashboard/data.py` |

**`data.py` spec:**
```python
# Function: get_dashboard_summary(db, user_id="default") -> dict
#   Returns all data needed for trust dashboard:
#   {
#     "memory": {
#       "total_nodes": count,
#       "by_epistemic_status": { "verified": N, "inferred": N, ... },
#       "by_scope": { "personal": N, "work": N, "family": N },
#       "total_episodes": count
#     },
#     "costs": {
#       "today_usd": float,
#       "this_week_usd": float,
#       "this_month_usd": float,
#       "by_task_type": { "chat": float, "sub_agent": float, ... }
#     },
#     "failures": {
#       "total": count, "resolved": count, "unresolved": count,
#       "by_type": { "factual_error": N, "preference_miss": N, ... },
#       "improvement_rate": float  # resolved / total
#     },
#     "skills": {
#       "total": count, "promoted": count,
#       "top_5_by_usage": [{ name, usage_count, success_rate }]
#     },
#     "intents": {
#       "active": count, "completed": count, "abandoned": count
#     },
#     "personality": {
#       "sliders": { name: value, ... },
#       "preset": "buddy" | "engineer" | "powerhouse" | "custom",
#       "estimated_monthly_cost": float
#     }
#   }
```

### STEP 5.2: Personality Versioning + Drift Detection

| # | Task | File |
|---|---|---|
| 5.2.1 | Create personality tracker | `src/windyfly/personality/versioning.py` |

**`versioning.py` spec:**
```python
# Function: snapshot_personality(db, user_id="default", changed_by="user")
#   Read all current soul rows
#   For each: INSERT into soul_history (soul_id, old_value=current, new_value=current, changed_by)
#   This creates a versioned checkpoint
#
# Function: get_personality_history(db, limit=10) -> list[dict]
#   SELECT from soul_history ORDER BY created_at DESC
#   Returns timeline of personality changes
#
# Function: detect_drift(db, user_id="default") -> dict | None
#   Compare current soul values vs values from 30 days ago
#   If any slider changed > 2 points without explicit user action:
#     Return { "drifted_sliders": [{ name, old, new }], "drift_source": "agent_evolution" }
#   If no significant drift: return None
#
# Function: rollback_personality(db, snapshot_date: str)
#   Find soul_history entries closest to snapshot_date
#   Restore soul table to those values
#   Log the rollback in soul_history with changed_by="rollback"
```

### STEP 5.3: Automated Skill Evaluation (v2)

| # | Task | File |
|---|---|---|
| 5.3.1 | Create golden test runner | `src/windyfly/skills/golden_tests.py` |

**`golden_tests.py` spec:**
```python
# Golden tests are stored as JSON in the skill's eval_results field:
# { "golden_tests": [
#     { "input": "...", "expected_output": "...", "passed": bool }
# ]}
#
# Function: run_golden_tests(db, skill_id) -> dict
#   Load skill code + golden tests from eval_results
#   For each test: execute_in_sandbox(code, input=test.input)
#   Compare output to expected_output (fuzzy match for text, exact for data)
#   Return: { "passed": N, "failed": N, "total": N, "results": [...] }
#
# Function: run_regression_suite(db) -> dict
#   For every promoted skill: run_golden_tests()
#   If any previously passing skill now fails:
#     Return { "regressions": [{ skill_name, failed_tests }] }
#   Alert user about regressions
```

### STEP 5.4: Observability

| # | Task | File |
|---|---|---|
| 5.4.1 | Create event logger | `src/windyfly/observability/events.py` |

**`events.py` spec:**
```python
# Structured JSON logging to both console and SQLite
#
# Function: log_event(db, write_queue, event_type: str, data: dict)
#   event_type: "agent.respond", "memory.write", "skill.evaluate",
#               "cost.log", "failure.detect", "intent.surface",
#               "conflict.detect", "decay.run", "matrix.message"
#   Writes to a lightweight events table (create in migration v3):
#   CREATE TABLE events (
#       id INTEGER PRIMARY KEY AUTOINCREMENT,
#       event_type TEXT NOT NULL,
#       data JSON,
#       created_at DATETIME DEFAULT CURRENT_TIMESTAMP
#   );
#   Prune events older than 30 days on each run
#   Priority: LOW
```

### STEP 5.5: Tests + Final Commit

| # | Task |
|---|---|
| 5.5.1 | Test dashboard data (verify all counts) |
| 5.5.2 | Test personality versioning (snapshot, diff, rollback) |
| 5.5.3 | Test golden tests runner |
| 5.5.4 | Run full suite: `uv run pytest tests/ -v` |
| 5.5.5 | Commit + tag v1.0.0 |

**🎯 PHASE 5 COMPLETE CRITERIA:**
- [ ] Dashboard endpoint returns complete agent state summary
- [ ] Personality snapshots saved, drift detection works
- [ ] Personality rollback restores previous state
- [ ] Golden tests run against all promoted skills
- [ ] Regression detection catches broken skills
- [ ] Event logging captures all system activity
- [ ] All tests pass, tagged v1.0.0

---

## 11. WINDY ECOSYSTEM INTEGRATION MAP

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        WINDY FLY (windy-agent/)                        │
│                                                                         │
│  Brain (Python)                    Gateway (Bun) — Phase 4+            │
│  ├── Agent Loop                    ├── Web UI on :3000                  │
│  ├── Memory (SQLite)               ├── WebSocket chat                   │
│  ├── Skills Engine                 └── REST API for sliders/cost        │
│  ├── Control Panel                      │                               │
│  └── Matrix Bot ◄──── UDS ───────────────┘                              │
│         │                                                                │
└─────────┼────────────────────────────────────────────────────────────────┘
          │ Matrix Protocol (matrix-nio)
          │
┌─────────┼────────────────────────────────────────────────────────────────┐
│         ▼                                                                │
│  SYNAPSE HOMESERVER (chat.windypro.com:8008)                            │
│  ├── @windyfly:chat.windypro.com (bot user)                             │
│  ├── All Windy Chat rooms (E2E encrypted)                               │
│  └── Presence, typing, read receipts                                    │
│         │                                                                │
│  CHAT SERVICES                                                          │
│  ├── K2 Onboarding (:8101) ──── provisions @windyfly Matrix account     │
│  ├── K3 Directory  (:8102) ──── registers @windyfly as discoverable     │
│  ├── K6 Push       (:8103) ──── pushes agent responses to mobile        │
│  └── K8 Backup     (:8104) ──── backs up agent conversations            │
└──────────────────────────────────────────────────────────────────────────┘
          │
          │ REST API (httpx)
          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  WINDY PRO ACCOUNT SERVER (windy-pro/account-server :8098)              │
│                                                                          │
│  Endpoints used by Windy Fly:                                           │
│  ├── GET  /api/v1/user/history       → translation history              │
│  ├── GET  /api/v1/recordings/list    → voice recordings                 │
│  ├── GET  /api/v1/clone/training-data → clone readiness                 │
│  ├── POST /api/v1/translate/text     → translate on demand              │
│  ├── GET  /api/v1/translate/languages → supported languages             │
│  └── GET  /api/v1/auth/me            → verify JWT / user identity       │
│                                                                          │
│  Auth: JWT token from Windy Pro account system                          │
│  Agent stores token in windyfly.toml [windy_api] section                │
└──────────────────────────────────────────────────────────────────────────┘
          │
          │ Shared contracts (optional npm package)
          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  WINDY PRO DESKTOP (windy-pro/)                                         │
│  ├── shared/contracts/ ──── TypeScript types for API, sessions, etc.    │
│  ├── src/engine/ ──── Whisper transcription (Python, same runtime)      │
│  └── WindySense ──── adaptive model selection (agent can query/tune)     │
│                                                                          │
│  WINDY PRO MOBILE (windy-pro-mobile/)                                   │
│  ├── src/services/chatClient.ts ──── same Matrix protocol as agent      │
│  └── src/stores/ ──── Zustand state (agent syncs via Matrix)            │
│                                                                          │
│  WINDY CLOUD (windy-pro-cloud/)                                         │
│  └── Distributed file storage ──── agent can store/retrieve files       │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 12. MASTER FILE INDEX

| Phase | File | Purpose |
|---|---|---|
| 0 | `pyproject.toml` | uv project config, dependencies |
| 0 | `windyfly.toml` | Runtime config (sliders, budgets, model) |
| 0 | `SOUL.md` | Personality definition |
| 0 | `src/windyfly/config.py` | TOML loader + env merge |
| 0 | `src/windyfly/main.py` | Entry point (CLI or Matrix) |
| 0 | `src/windyfly/memory/database.py` | SQLite connection + migrations |
| 0 | `src/windyfly/memory/episodes.py` | Episode CRUD + FTS search |
| 0 | `src/windyfly/memory/nodes.py` | Knowledge node CRUD |
| 0 | `src/windyfly/memory/soul.py` | Soul/personality CRUD |
| 0 | `src/windyfly/memory/skills.py` | Skills table CRUD |
| 0 | `src/windyfly/memory/failures.py` | Failure log CRUD |
| 0 | `src/windyfly/memory/cost_ledger.py` | Cost tracking CRUD |
| 0 | `src/windyfly/memory/write_queue.py` | Priority write queue |
| 0 | `src/windyfly/personality/engine.py` | SOUL.md parser + injection |
| 0 | `src/windyfly/personality/mode.py` | Mode switching |
| 0 | `src/windyfly/agent/models.py` | LLM provider abstraction |
| 0 | `src/windyfly/agent/prompt.py` | Prompt assembly |
| 0 | `src/windyfly/agent/loop.py` | Core ReAct loop |
| 0 | `src/windyfly/channels/cli.py` | CLI channel |
| 1 | `src/windyfly/channels/matrix_bot.py` | Matrix/Synapse bot |
| 1 | `src/windyfly/tools/registry.py` | Tool registration |
| 1 | `src/windyfly/tools/windy_api.py` | Windy Pro API tools |
| 2 | `src/windyfly/soul_import/openclaw.py` | OpenClaw parser |
| 2 | `src/windyfly/soul_import/hermes.py` | Hermes parser |
| 2 | `src/windyfly/soul_import/chatgpt.py` | ChatGPT parser |
| 2 | `src/windyfly/soul_import/orchestrator.py` | Import orchestrator |
| 2 | `src/windyfly/soul_import/preview.py` | Soul Preview formatter |
| 2 | `src/windyfly/control_panel.py` | Presets + sliders + cost estimate |
| 2 | `src/windyfly/agent/failure_detector.py` | Friction detection |
| 3 | `src/windyfly/skills/manager.py` | Skill lifecycle |
| 3 | `src/windyfly/skills/sandbox.py` | Sandboxed execution |
| 3 | `src/windyfly/skills/evaluator.py` | 3-gate evaluation |
| 3 | `src/windyfly/memory/cost_tracker.py` | Budget enforcement |
| 3 | `src/windyfly/memory/intents.py` | Intent CRUD |
| 3 | `src/windyfly/agent/intent_detector.py` | Intent extraction |
| 3 | `src/windyfly/agent/emotion_detector.py` | Emotional awareness |
| 4 | `src/windyfly/bridge/uds_server.py` | UDS bridge (Brain side) |
| 4 | `src/windyfly/memory/decay.py` | Cognitive decay engine |
| 4 | `src/windyfly/memory/conflict_detector.py` | Contradiction detection |
| 4 | `src/windyfly/agent/sub_agents.py` | Sub-agent orchestration |
| 4 | `src/windyfly/agent/offline.py` | Offline fallback |
| 4 | `gateway/src/bridge.ts` | UDS client (Gateway side) |
| 4 | `gateway/src/server.ts` | Bun HTTP server |
| 4 | `gateway/src/websocket.ts` | WebSocket handler |
| 5 | `src/windyfly/dashboard/data.py` | Dashboard data aggregation |
| 5 | `src/windyfly/personality/versioning.py` | Personality snapshots + drift |
| 5 | `src/windyfly/skills/golden_tests.py` | Regression test runner |
| 5 | `src/windyfly/observability/events.py` | Structured event logging |

**Total:** ~47 source files across 5 phases.

---

## 13. ROADMAP TIMELINE

| Phase | Weeks | Deliverable | Tag |
|---|---|---|---|
| **0** | 0–1 | Python agent loop + CLI + SQLite + personality | v0.1.0 |
| **1** | 1–2 | Matrix bot in Windy Chat + Windy Pro API tools | v0.2.0 |
| **2** | 2–4 | Soul Continuity + Control Panel + Truth Layer + NW2 | v0.3.0 |
| **3** | 4–8 | Skills + Cost Ledger + Intents + Emotional Awareness | v0.4.0 |
| **4** | 8–14 | Bun Gateway + Decay + Conflicts + Sub-Agents + Offline | v0.5.0 |
| **5** | 14–18 | Dashboard + Personality Versioning + Auto Eval + Observability | v1.0.0 |

---

## 14. GAP ANALYSIS CHECKLIST

> Run this checklist after each phase. Every `[ ]` must become `[x]`. Any remaining `[ ]` is a gap to close.

### Phase 0 Codons
- [ ] C0.1: Repo initialized with uv, all directories created
- [ ] C0.2: `windyfly.toml` parsed correctly by config loader
- [ ] C0.3: All 6 SQL tables created by migration v1
- [ ] C0.4: Episodes saved and retrieved (CRUD verified)
- [ ] C0.5: Nodes upserted and searched (CRUD verified)
- [ ] C0.6: SOUL.md loaded and injected into system prompt
- [ ] C0.7: LLM call succeeds with at least one provider
- [ ] C0.8: Agent responds via CLI with personality
- [ ] C0.9: Agent remembers previous messages in session
- [ ] C0.10: Cost logged to cost_ledger table
- [ ] C0.11: Write queue processes HIGH before MEDIUM before LOW
- [ ] C0.12: All pytest tests pass

### Phase 1 Codons
- [ ] C1.1: matrix-nio installed and imports
- [ ] C1.2: Bot logs into Synapse homeserver
- [ ] C1.3: Bot auto-accepts room invites
- [ ] C1.4: Bot responds to DMs with agent personality
- [ ] C1.5: Typing indicator shows during processing
- [ ] C1.6: Bot presence shows online/offline
- [ ] C1.7: Windy translation metadata (`windy_lang`) attached
- [ ] C1.8: Reconnection works after network drop
- [ ] C1.9: Tool registry has 4 Windy API tools
- [ ] C1.10: Tools callable by agent via function calling

### Phase 2 Codons
- [ ] C2.1: OpenClaw export parsed successfully
- [ ] C2.2: Hermes export parsed successfully
- [ ] C2.3: ChatGPT export parsed successfully
- [ ] C2.4: Soul Preview displays before any write
- [ ] C2.5: User can approve/reject/review-sensitive
- [ ] C2.6: Imported data has correct confidence and source
- [ ] C2.7: Three presets (buddy/engineer/powerhouse) apply correctly
- [ ] C2.8: Individual sliders writable and readable
- [ ] C2.9: Cost estimation returns breakdown by slider
- [ ] C2.10: Epistemic strictness filters nodes in prompt
- [ ] C2.11: Friction detected on user corrections
- [ ] C2.12: Failures logged with fault_type classification
- [ ] C2.13: Recurring failures trigger proactive behavior

### Phase 3 Codons
- [ ] C3.1: Skill created and stored in DB
- [ ] C3.2: Skill evaluated (syntax gate)
- [ ] C3.3: Skill evaluated (execution gate)
- [ ] C3.4: Skill evaluated (safety gate)
- [ ] C3.5: Skill promoted after passing all gates
- [ ] C3.6: Skill rolled back to parent version
- [ ] C3.7: Cost logged per LLM call with model + tokens
- [ ] C3.8: Daily budget enforced, agent warns before exceeding
- [ ] C3.9: Migration v2 adds 4 new tables
- [ ] C3.10: Intent detected from conversation
- [ ] C3.11: Inferred intents surface within 24 hours
- [ ] C3.12: Intent decay reduces stale goal scores
- [ ] C3.13: Emotional context detected per message
- [ ] C3.14: Sustained stress triggers behavior adjustment

### Phase 4 Codons
- [ ] C4.1: UDS bridge transmits JSON between Brain and Gateway
- [ ] C4.2: Bun gateway serves HTTP on port 3000
- [ ] C4.3: WebSocket chat works end-to-end
- [ ] C4.4: Cognitive decay reduces old node scores
- [ ] C4.5: Aged content pruned (decay_score < 0.05 deleted)
- [ ] C4.6: Conflict detected on contradicting node update
- [ ] C4.7: User prompted to resolve conflict
- [ ] C4.8: Sub-agent executes within token budget
- [ ] C4.9: Sub-agent depth limited to 1
- [ ] C4.10: Offline fallback returns local model response or queue

### Phase 5 Codons
- [ ] C5.1: Dashboard returns complete summary JSON
- [ ] C5.2: Personality snapshot saved to soul_history
- [ ] C5.3: Drift detection flags unauthorized changes
- [ ] C5.4: Personality rollback restores previous state
- [ ] C5.5: Golden tests run against promoted skills
- [ ] C5.6: Regression suite catches broken skills
- [ ] C5.7: Event logger captures all system events
- [ ] C5.8: Events pruned after 30 days

---

**🧬 END OF DNA STRAND MASTER PLAN (Parts 1–4)**

**Total Codons (P0–P5):** 59
**Total Source Files:** ~47
**Total Phases (P0–P5):** 6 (0–5)
**Estimated Timeline:** 18 weeks to v1.0.0
**Verification Method:** Codon checklist gap analysis after each phase

> **📌 See [Part 5](WINDY_FLY_DNA_STRAND_PART5.md) for the Beyond-Blueprint Amendment**
> — 34 additional codons documenting shape-shift engine, provider management,
> Mission Control, extended AI features, and the full API surface gap closure.

> *"If you have one copy of the DNA, you can recreate the entire organism."*
> *This document is that copy. Every ribosome gets the same instructions. Every cell builds the same Windy Fly.*

