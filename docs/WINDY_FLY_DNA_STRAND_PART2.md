# 🧬 WINDY FLY — DNA STRAND MASTER PLAN

## Part 2 of 4: Phases 1–2 (Matrix + Soul + Control Panel)

---

## 6. PHASE 1 — MATRIX BOT + WINDY CHAT (Week 1–2)

> **GOAL:** Windy Fly lives inside Windy Chat as `@windyfly:chat.windychat.ai`. Users open Windy Chat, see Windy Fly as a contact, and chat with their agent. Messages sync across desktop and mobile. Agent shows presence (online/offline) and typing indicators.

### STEP 1.1: Matrix Bot Channel

| # | Task | File |
|---|---|---|
| 1.1.1 | Install matrix-nio | `uv add "matrix-nio[e2e]>=0.24"` — verify with `uv run python -c "import nio; print(nio.__version__)"` |
| 1.1.2 | Create Matrix bot | `src/windyfly/channels/matrix_bot.py` |
| 1.1.3 | Add Matrix config to `windyfly.toml` | Already present from Phase 0 (`[matrix]` section) — verify it exists |
| 1.1.4 | Update `main.py` to support `--channel matrix` flag | `src/windyfly/main.py` |

**`matrix_bot.py` spec:**
```python
# Class: WindyFlyMatrixBot
#
# __init__(self, config: dict, db: Database, write_queue: WriteQueue)
#   - Stores config, db, write_queue
#   - Creates nio.AsyncClient with config["matrix"]["homeserver"]
#   - Sets device_name = "Windy Fly Agent"
#
# async login(self)
#   - If MATRIX_BOT_TOKEN env var set: use token login (nio client.access_token = token)
#   - Else: use password login (MATRIX_BOT_PASSWORD env var)
#   - On success: log "Windy Fly logged in as @windyfly:chat.windychat.ai"
#   - On failure: raise with clear error message
#
# async _on_message(self, room: nio.MatrixRoom, event: nio.RoomMessageText)
#   - IGNORE messages from self (event.sender == self bot user id)
#   - IGNORE messages older than 30 seconds (prevent processing backlog on reconnect)
#   - Extract: sender_id, display_name, room_id, body, timestamp
#   - Extract Windy metadata: event.source.get("content", {}).get("windy_lang")
#   - Generate or reuse session_id per room_id (dict mapping room_id → session_id)
#   - Call agent_respond(config, db, write_queue, body, session_id)
#   - Send response back to room: await client.room_send(room_id, "m.room.message", {
#       "msgtype": "m.text",
#       "body": response_text,
#       "windy_original": response_text,
#       "windy_lang": "en"  # agent's language
#     })
#   - Set typing indicator OFF after response
#
# async _on_invite(self, room: nio.MatrixRoom, event: nio.InviteMemberEvent)
#   - Auto-accept all invites: await client.join(room.room_id)
#   - Send welcome message: "Hey! I'm Windy Fly, your personal AI companion. 🪰"
#
# async start(self)
#   - await self.login()
#   - Register callbacks: client.add_event_callback(self._on_message, nio.RoomMessageText)
#   - Register invite callback: client.add_event_callback(self._on_invite, nio.InviteMemberEvent)
#   - Set presence to online
#   - await client.sync_forever(timeout=30000, full_state=True)
#
# async stop(self)
#   - Set presence to offline
#   - Close client
```

**`main.py` update spec:**
```python
# Add argparse:
#   --channel: "cli" (default) or "matrix"
#
# if channel == "cli": run_cli(config)
# if channel == "matrix":
#   bot = WindyFlyMatrixBot(config, db, write_queue)
#   asyncio.run(bot.start())
```

**Verify 1.1:** `uv run python -m windyfly.main --channel matrix` → bot logs in, responds to DMs in Windy Chat.

### STEP 1.2: Typing Indicators + Presence

| # | Task | Where |
|---|---|---|
| 1.2.1 | Add typing indicator | In `_on_message`: before calling `agent_respond()`, send typing ON: `await client.room_typing(room_id, True, timeout=10000)` |
| 1.2.2 | Set presence on start | In `start()`: `await client.set_presence("online")` |
| 1.2.3 | Set presence on stop | In `stop()`: `await client.set_presence("offline")` |

### STEP 1.3: Offline Queue + Reconnection

| # | Task | Where |
|---|---|---|
| 1.3.1 | Add reconnection wrapper | Wrap `sync_forever` in a retry loop: on `nio.exceptions.TransportError`, wait exponential backoff (1s, 2s, 4s... max 30s), then retry. Log each attempt. |
| 1.3.2 | Add pending response queue | If `agent_respond()` or `room_send()` fails due to network: store `(room_id, response)` in a `_pending_responses` list. On next successful sync, flush all pending. |

### STEP 1.4: Windy Pro API Tools

| # | Task | File |
|---|---|---|
| 1.4.1 | Create tool registry | `src/windyfly/tools/registry.py` |
| 1.4.2 | Create Windy API tools | `src/windyfly/tools/windy_api.py` |

**`registry.py` spec:**
```python
# Class: ToolRegistry
#   tools: dict[str, Callable]   # name → function
#   schemas: list[dict]          # OpenAI function-calling schemas
#
# register(self, name: str, description: str, parameters: dict, fn: Callable)
# get_schemas(self) -> list[dict]   # Returns OpenAI-format tool schemas
# execute(self, name: str, arguments: dict) -> str   # Calls fn, returns result as string
```

**`windy_api.py` spec — 4 initial tools:**
```python
# Tool 1: get_translation_history(limit=10)
#   GET {WINDY_API_URL}/api/v1/user/history
#   Headers: Authorization: Bearer {WINDY_JWT}
#   Returns: list of recent translations
#
# Tool 2: get_recordings(limit=10)
#   GET {WINDY_API_URL}/api/v1/recordings/list
#   Returns: list of voice recordings
#
# Tool 3: get_clone_status()
#   GET {WINDY_API_URL}/api/v1/clone/training-data
#   Returns: clone readiness, phoneme coverage, hours recorded
#
# Tool 4: translate_text(text: str, source_lang: str, target_lang: str)
#   POST {WINDY_API_URL}/api/v1/translate/text
#   Body: { text, source_lang, target_lang }
#   Returns: translated text
```

### STEP 1.5: Tests + Commit

| # | Task |
|---|---|
| 1.5.1 | Write `tests/test_matrix_bot.py` — mock nio client, test message handling, invite acceptance, pending queue flush |
| 1.5.2 | Write `tests/test_tools.py` — mock httpx, test tool registry and Windy API tool execution |
| 1.5.3 | Run all tests: `uv run pytest tests/ -v` |
| 1.5.4 | Commit: `git add -A && git commit -m "feat: Phase 1 — Matrix bot + Windy Chat integration + tools"` |
| 1.5.5 | Tag: `git tag v0.2.0 -m "Phase 1: Matrix bot live in Windy Chat"` |

**🎯 PHASE 1 COMPLETE CRITERIA:**
- [ ] `uv run python -m windyfly.main --channel matrix` connects to Synapse
- [ ] User messages agent in Windy Chat → gets response
- [ ] Typing indicator shows while agent thinks
- [ ] Agent shows as "online" in contact list
- [ ] Agent auto-accepts room invites
- [ ] Translation metadata (`windy_lang`) attached to all responses
- [ ] Reconnects automatically on network failure
- [ ] Windy Pro API tools callable by agent
- [ ] All tests pass, tagged v0.2.0

---

## 7. PHASE 2 — SOUL CONTINUITY + CONTROL PANEL + TRUTH LAYER (Week 2–4)

> **GOAL:** Agent imports user data from other agents (Soul Preview UX), exposes Control Panel sliders, and applies epistemic status to all facts. "Never Wrong Twice" failure system goes live.

### STEP 2.1: Soul Continuity Engine

| # | Task | File |
|---|---|---|
| 2.1.1 | Create import parsers | `src/windyfly/soul_import/` (new directory) |
| 2.1.2 | OpenClaw parser | `src/windyfly/soul_import/openclaw.py` |
| 2.1.3 | Hermes parser | `src/windyfly/soul_import/hermes.py` |
| 2.1.4 | ChatGPT parser | `src/windyfly/soul_import/chatgpt.py` |
| 2.1.5 | Import orchestrator | `src/windyfly/soul_import/orchestrator.py` |
| 2.1.6 | Soul Preview formatter | `src/windyfly/soul_import/preview.py` |

**`openclaw.py` spec:**
```python
# Function: parse_openclaw(export_path: str) -> dict
#   Reads: SOUL.md, MEMORY.md, skills/*.md, config.yaml
#   Returns: {
#     "personality": { "traits": [...], "humor": int, ... },
#     "memories": [ { "type": "fact", "content": "...", "confidence": 0.5 } ],
#     "skills": [ { "name": "...", "code": "...", "language": "..." } ],
#     "source": "openclaw"
#   }
#   All imported items get: confidence=0.5, source="imported_openclaw"
```

**`hermes.py` spec:**
```python
# Function: parse_hermes(export_path: str) -> dict
#   Reads: SQLite sessions DB, MEMORY.md, skills/
#   Returns same format as openclaw parser
#   Source: "imported_hermes"
```

**`chatgpt.py` spec:**
```python
# Function: parse_chatgpt(export_path: str) -> dict
#   Reads: conversations.json from ChatGPT data export
#   Extracts: user preferences, topics discussed, communication style
#   No skills (ChatGPT doesn't export them)
#   Source: "imported_chatgpt"
```

**`preview.py` — Soul Preview UX (the most important UX in the app):**
```python
# Function: format_soul_preview(parsed_data: dict) -> str
#   Returns a human-readable summary for user approval:
#   """
#   🧬 Soul Preview — Import from [source]
#
#   📝 Personality traits found: 5
#     - Humor level: 7/10
#     - Formality: 3/10
#     - Communication style: casual
#
#   🧠 Memories found: 23
#     ✅ Safe (auto-import): 18 preferences, settings
#     ⚠️ Sensitive (needs review): 4 beliefs, identity facts
#     🔒 Executable (sandbox required): 1 skill
#
#   📊 Confidence: All imports marked at 50% confidence
#     You can verify or dismiss any imported fact later.
#
#   Would you like to proceed? (yes/no/review-sensitive)
#   """
```

**`orchestrator.py` spec:**
```python
# Function: import_soul(db: Database, export_path: str,
#           source_type: str, user_approved: bool = False) -> dict
#   1. Detect source_type or auto-detect from file structure
#   2. Call appropriate parser
#   3. Generate Soul Preview via format_soul_preview()
#   4. If not user_approved: return preview dict (don't write anything)
#   5. If user_approved:
#      a. Safe items → upsert_node() with confidence=0.5, source="imported_*"
#      b. Sensitive items → upsert_node() with confidence=0.3, flag for review
#      c. Executable items → save to skills table with promoted=FALSE
#   6. Return summary: { imported: N, flagged: N, skipped: N }
```

### STEP 2.2: Control Panel ("The Cockpit")

| # | Task | File |
|---|---|---|
| 2.2.1 | Create control panel module | `src/windyfly/control_panel.py` |
| 2.2.2 | Create preset definitions | Inside `control_panel.py` |

**`control_panel.py` spec:**
```python
# PRESETS (constant dict):
PRESETS = {
    "buddy":      { "personality": 8, "reasoning_depth": 5, "memory_depth": 6,
                    "proactivity": 7, "autonomy": 4, "verbosity": 6,
                    "epistemic_strictness": 4 },
    "engineer":   { "personality": 3, "reasoning_depth": 8, "memory_depth": 7,
                    "proactivity": 3, "autonomy": 5, "verbosity": 4,
                    "epistemic_strictness": 7 },
    "powerhouse": { "personality": 9, "reasoning_depth": 9, "memory_depth": 9,
                    "proactivity": 8, "autonomy": 7, "verbosity": 7,
                    "epistemic_strictness": 6 },
}

# Function: apply_preset(db: Database, preset_name: str, user_id: str = "default")
#   Reads PRESETS[preset_name], writes each slider as a soul row:
#   upsert_soul(db, key=f"slider_{slider_name}", value=str(val), source="control_panel")
#
# Function: set_slider(db: Database, slider_name: str, value: int, user_id: str = "default")
#   Validates: 1 <= value <= 10
#   Writes: upsert_soul(db, key=f"slider_{slider_name}", value=str(value), source="control_panel")
#
# Function: get_sliders(db: Database, user_id: str = "default") -> dict
#   Reads all soul rows where key starts with "slider_"
#   Returns: { "personality": 7, "reasoning_depth": 6, ... }
#   Falls back to config file defaults for missing sliders
#
# Function: estimate_monthly_cost(sliders: dict) -> dict
#   Returns: { "estimated_usd": float, "breakdown": { slider: cost } }
#   Cost model (approximate):
#     personality:          value * 0.30/month
#     reasoning_depth:      value * 0.50/month
#     memory_depth:         value * 0.80/month
#     proactivity:          value * 0.50/month
#     autonomy:             0 (risk, not cost)
#     verbosity:            value * 0.20/month
#     epistemic_strictness: 0 (filtering, not cost)
```

### STEP 2.3: Truth Layer Enforcement

| # | Task | File |
|---|---|---|
| 2.3.1 | Update prompt assembly | `src/windyfly/agent/prompt.py` |
| 2.3.2 | Update node display | `src/windyfly/memory/nodes.py` |

**Updates to `prompt.py`:**
- When including nodes in context, prefix each with its epistemic status:
  - `[VERIFIED]`, `[USER_STATED]`, `[INFERRED]`, `[SPECULATIVE]`, `[CONTRADICTED]`
- If `epistemic_strictness` slider > 7: exclude `[SPECULATIVE]` and `[INFERRED]` nodes from context
- If slider > 9: only include `[VERIFIED]` and `[USER_STATED]` nodes
- Add system instruction: "When you state a fact, indicate your confidence level. If a fact is marked INFERRED, say so."

### STEP 2.4: "Never Wrong Twice" System

| # | Task | File |
|---|---|---|
| 2.4.1 | Create failure detector | `src/windyfly/agent/failure_detector.py` |

**`failure_detector.py` spec:**
```python
# FRICTION_PATTERNS (list of regex + classification):
FRICTION_PATTERNS = [
    (r"(?i)(no|wrong|incorrect|that'?s not|actually)", "factual_error"),
    (r"(?i)(i (said|told you|meant))", "preference_miss"),
    (r"(?i)(try again|redo|retry|one more time)", "execution_failure"),
    (r"(?i)(what i meant was|let me clarify)", "ambiguity_mishandled"),
]

# Function: detect_friction(user_message: str, previous_agent_message: str) -> dict | None
#   Checks user_message against FRICTION_PATTERNS
#   If match found, returns:
#     { "fault_type": "factual_error", "user_message": "...",
#       "agent_message": "...", "pattern_matched": "..." }
#   If no match: returns None
#
# Function: log_failure(db: Database, write_queue: WriteQueue, friction: dict)
#   Enqueues INSERT into failures table with fault_type, description (user + agent messages)
#   Priority: HIGH
#
# Function: check_recurring_failure(db: Database, fault_type: str, description: str) -> bool
#   Checks if similar failure exists in last 7 days
#   Returns True if recurring → agent should proactively address it
```

**Integration into `loop.py`:**
- After receiving user message, before calling LLM:
  1. Call `detect_friction(user_message, last_agent_response)`
  2. If friction detected: `log_failure()` and prepend to system prompt: "The user just corrected you. Review what went wrong."
  3. If `check_recurring_failure()` returns True: add extra instruction: "This is a recurring issue. Be extra careful."

### STEP 2.5: Tests + Commit

| # | Task |
|---|---|
| 2.5.1 | Write `tests/test_soul_import.py` — test each parser with sample data, test preview formatting, test orchestrator with mock approval |
| 2.5.2 | Write `tests/test_control_panel.py` — test preset application, slider bounds validation, cost estimation |
| 2.5.3 | Write `tests/test_failure_detector.py` — test friction patterns, failure logging, recurrence detection |
| 2.5.4 | Run all tests: `uv run pytest tests/ -v` |
| 2.5.5 | Commit: `git add -A && git commit -m "feat: Phase 2 — Soul Continuity + Control Panel + Truth Layer + Never Wrong Twice"` |
| 2.5.6 | Tag: `git tag v0.3.0 -m "Phase 2: Soul import, cockpit sliders, truth layer, failure system"` |

**🎯 PHASE 2 COMPLETE CRITERIA:**
- [ ] `import_soul()` parses OpenClaw/Hermes/ChatGPT exports
- [ ] Soul Preview shows before any data written
- [ ] User can approve/reject imports
- [ ] Imported data has confidence=0.5, source="imported_*"
- [ ] `apply_preset("buddy")` sets all sliders in soul table
- [ ] `set_slider("personality", 8)` writes to DB, affects prompt assembly
- [ ] `estimate_monthly_cost()` returns cost breakdown
- [ ] Prompt assembly respects `epistemic_strictness` slider
- [ ] Friction detection catches user corrections
- [ ] Failures logged to `failures` table with `fault_type`
- [ ] Recurring failures trigger proactive agent behavior
- [ ] All tests pass, tagged v0.3.0

---

> **CONTINUES IN PART 3:** Phase 3 (Skill Engine + Cost Ledger + Intent System), Phase 4 (Bun Gateway + Advanced Features)
