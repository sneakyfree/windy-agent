# 🧬 WINDY FLY — DNA STRAND MASTER PLAN: PART 5

## Strand Amendment — Beyond-Blueprint Features

> **Created:** 2026-03-27 · **Reason:** Gap analysis revealed 23 features that were implemented
> but never documented in the original DNA Strand Master Plan (Parts 1–4).
> This amendment retroactively codifies these features into the blueprint so
> every future ribosome session has a complete picture.

---

## Phase 6: Shape-Shift Engine

### C6.1 — Shape-Shift Context Manager

**Files:** `src/windyfly/agent/shape_shift.py`

The agent can temporarily reconfigure its personality sliders for a specialist task
while preserving full memory and conversation context. After the task, the original
personality is restored automatically.

- Cost advantage: 50% fewer tokens vs. isolated sub-agents
- Supports nested shape-shifts via a stack-based slider save/restore
- Integrated as an LLM-callable tool (`shape_shift` / `shape_shift_restore`)

### C6.2 — Shape-Shift Autonomy Gating

The agent's behavior during shape-shift is gated by the `autonomy` slider:

| Autonomy | Behavior |
|---|---|
| 0–3 | Asks permission: presents Option A (shift) vs Option B (sub-agent) |
| 4–6 | Announces the shift, proceeds autonomously |
| 7–10 | Shifts silently |

### C6.3 — Shape-Shift Bias Slider

**Slider:** `shape_shift_bias` (0–10)

Controls whether the agent prefers shape-shifting (high) or sub-agents (low)
when specialist work is needed. Included in all 8 presets.

- 0–3: Always spawns isolated sub-agent (clean slate, 2x tokens)
- 7–10: Always shape-shifts in place (keeps memory, half tokens)

### Codons
- [ ] C6.1: Shape-shift context manager saves/restores sliders
- [ ] C6.2: Autonomy gating produces correct announcement text
- [ ] C6.3: Shape-shift bias slider registered in control panel with all 8 presets
- [ ] C6.4: `shape_shift` / `shape_shift_restore` tools registered in tool registry
- [ ] C6.5: Gateway routes `/api/shape-shift` and `/api/shape-shift/restore` work

---

## Phase 7: Provider Management System

### C7.1 — Multi-Provider Registry

**Files:** `src/windyfly/agent/providers.py`, `gateway/src/providers.ts`

Supports 11 built-in LLM providers out of the box:

| Provider | Type | API |
|---|---|---|
| OpenAI | Native | `api.openai.com` |
| Anthropic | Native | `api.anthropic.com` |
| xAI Grok | OpenAI-compatible | `api.x.ai` |
| Google Gemini | OpenAI-compatible | `generativelanguage.googleapis.com` |
| DeepSeek | OpenAI-compatible | `api.deepseek.com` |
| Mistral | OpenAI-compatible | `api.mistral.ai` |
| OpenRouter | OpenAI-compatible | `openrouter.ai` |
| Together AI | OpenAI-compatible | `api.together.xyz` |
| Groq | OpenAI-compatible | `api.groq.com` |
| Perplexity | OpenAI-compatible | `api.perplexity.ai` |
| Fireworks AI | OpenAI-compatible | `api.fireworks.ai` |
| Ollama (Local) | OpenAI-compatible | `localhost:11434` |

### C7.2 — Dynamic Model Discovery

Live API introspection fetches available models from each provider.
Results are cached for 5 minutes and persisted to `data/providers.json`.

- Per-provider model fetching with response normalization
- Special handlers for Anthropic, Gemini, and OpenRouter APIs
- Batch discovery across all configured providers

### C7.3 — Multi-Key Management

Per-provider key vault stored in `data/provider_keys.json` (chmod 0600).

- Add/delete/activate keys
- Auto-activate first key added
- Key masking in API responses (first 8 + last 4 chars)

### C7.4 — OpenRouter OAuth PKCE Flow

Complete OAuth2 PKCE flow for OpenRouter:

1. `/api/providers/oauth/openrouter/start` → generates code_verifier + challenge
2. User redirected to OpenRouter authorization page
3. `/oauth/callback` → exchanges code for API key
4. Key auto-stored and models auto-discovered

### C7.5 — Provider Key Validation

`/api/providers/validate` tests an API key by attempting model discovery.
Returns valid/invalid status, model count, and account info (for OpenAI).

### C7.6 — Active Model Selection

Runtime model switching via `/api/providers/active-model` (PUT).
Stored in provider overrides for persistence across restarts.

### C7.7 — Custom Provider CRUD

Add/update/remove non-builtin providers via REST API.
Custom providers are stored in runtime overrides and merged with builtins.

### C7.8 — Anthropic OAuth Token Manager

**Files:** `src/windyfly/agent/oauth.py`

Manages Anthropic OAuth access tokens with automatic refresh:

- Token refresh 5 minutes before expiry
- Persistent cache to `data/.anthropic_oauth.json`
- Falls back to API key when OAuth not configured

### Codons
- [ ] C7.1: All 11 built-in providers registered
- [ ] C7.2: Model discovery returns live model list from at least one provider
- [ ] C7.3: Multi-key add/delete/activate works per-provider
- [ ] C7.4: OpenRouter OAuth PKCE flow completes end-to-end
- [ ] C7.5: Key validation returns correct status
- [ ] C7.6: Active model switch persists across restarts
- [ ] C7.7: Custom provider added and usable for LLM calls
- [ ] C7.8: Anthropic OAuth auto-refreshes expired tokens

---

## Phase 8: Mission Control — Machine Management

### C8.1 — Machine Registry

**Files:** `gateway/src/machines.ts`

Manages remote Windy Fly agent instances across multiple machines:

- Persistent config in `data/machines.json`
- WebSocket connections with auto-reconnect (5s on close, 10s on error)
- Health polling every 15 seconds

### C8.2 — Remote Machine CRUD

REST API for machine management:

| Route | Method | Description |
|---|---|---|
| `/api/machines` | GET | List all machines with status |
| `/api/machines` | POST | Add a new machine |
| `/api/machines/:id` | GET | Get single machine status |
| `/api/machines/:id` | PUT | Update machine config |
| `/api/machines/:id` | DELETE | Remove machine |

### C8.3 — Remote Terminal Relay

WebSocket PTY relay at `/ws/terminal/:machineId`. Bidirectional terminal
I/O proxied between the dashboard and remote agent daemons.

### C8.4 — Remote Service Control

- POST `/api/machines/:id/restart-gateway` — restart remote gateway
- POST `/api/machines/:id/restart-brain` — restart remote brain
- POST `/api/machines/:id/exec` — execute command on remote
- GET `/api/machines/:id/health` — get remote health data

### C8.5 — Provider Sync to Remote Machines

POST `/api/machines/sync-providers` pushes local provider config to all
connected remote machines or a specific target.

### Codons
- [ ] C8.1: Machine added and WebSocket connection established
- [ ] C8.2: Machine health polling returns data
- [ ] C8.3: Terminal relay forwards PTY I/O
- [ ] C8.4: Service restart command reaches remote
- [ ] C8.5: Provider sync propagates config to remote machine

---

## Phase 9: Extended AI Features

### C9.1 — Context Gas-Tank Header

**Files:** `src/windyfly/agent/context_header.py`

Signature Windy Fly feature: prepends a context usage header to responses.

**Format:** `[🪰 Windy Fly · Mar 27, 10:56 AM · 🟢 93%]`

Trigger conditions (OR'd):
- 1+ hour since last header
- 10%+ context delta since last header

Color coding: 🟢 ≥50%, 🟡 ≥10%, 🔴 <10%

### C9.2 — Relationship Moments

The agent extracts one-line emotional snapshots from emotionally charged
interactions. Stored as `type=relationship_moment` nodes.

**Format:** `"emotion → what happened → outcome"`

Triggers when `emotional_context != "neutral"` and `warmth >= 3`.

### C9.3 — Agent Journal

**Files:** Integrated into `loop.py`

The agent writes reflective diary entries from its own perspective.
Triggers every 10th interaction or when emotion is detected.
Entries stored as `type=journal_entry` nodes — browseable via `/api/journal`.

### C9.4 — Self-Assessment Report Card

**Files:** `src/windyfly/agent/self_assessment.py`

6-metric weekly performance grading:
- Memory accuracy, response quality, cost efficiency
- User satisfaction, skill health, uptime
Retrievable via POST `/api/assessment`.

### Codons
- [ ] C9.1: Context header displays with correct timestamp and percentage
- [ ] C9.2: Relationship moment saved after emotional interaction
- [ ] C9.3: Journal entry written on 10th interaction
- [ ] C9.4: Self-assessment returns 6-metric report

---

## Phase 10: Full API Surface (Gap Closure)

### C10.1 — Personality Versioning API

Routes added to expose existing backend capabilities:

| Route | Method | Description |
|---|---|---|
| `/api/personality/history` | GET | Personality change history |
| `/api/personality/snapshot` | POST | Create personality checkpoint |
| `/api/personality/drift` | GET | Detect unauthorized drift |
| `/api/personality/rollback` | POST | Rollback to previous state |

### C10.2 — Skills Management API

| Route | Method | Description |
|---|---|---|
| `/api/skills` | GET | List all skills |
| `/api/skills` | POST | Create a new skill |
| `/api/skills/:id/evaluate` | POST | Run 3-gate evaluation |
| `/api/skills/:id/promote` | POST | Promote after passing gates |
| `/api/skills/:id/rollback` | POST | Rollback to parent version |
| `/api/skills/:id/golden-tests` | POST | Run golden tests |
| `/api/skills/regression` | POST | Run full regression suite |

### C10.3 — System Internals API

| Route | Method | Description |
|---|---|---|
| `/api/decay/run` | POST | Trigger cognitive decay cycle |
| `/api/conflicts` | GET | List unresolved conflicts |
| `/api/conflicts/:id/resolve` | POST | Resolve a conflict |
| `/api/moments` | GET | Browse relationship moments |
| `/api/failures` | GET | List failure records |
| `/api/mode` | GET | Get current agent mode |
| `/api/mode` | PUT | Set agent mode (companion/focused/neutral) |
| `/api/offline/status` | GET | Check online + Ollama availability |
| `/api/events` | GET | System event log with 24h counts |

### C10.4 — Offline Message Queue

When the agent goes offline, messages are persisted to `data/offline_queue.json`
for processing when connectivity returns. Queue supports:

- `queue_message()` — add to queue
- `get_queued_messages()` — list queued
- `clear_queue()` — flush after processing

### Codons
- [ ] C10.1: Personality history returns soul_history entries
- [ ] C10.2: Personality snapshot creates checkpoint
- [ ] C10.3: Drift detection flags changed sliders
- [ ] C10.4: Skills list returns all skills from database
- [ ] C10.5: Skill evaluation runs 3-gate pipeline
- [ ] C10.6: Decay endpoint triggers and returns counts
- [ ] C10.7: Conflicts list returns unresolved entries
- [ ] C10.8: Conflict resolution updates database
- [ ] C10.9: Mode set validates and persists to soul table
- [ ] C10.10: Offline status reports connectivity and Ollama availability
- [ ] C10.11: Events endpoint returns logs with 24h counts
- [ ] C10.12: Offline queue persists messages to disk

---

## Updated Master Counts

| Metric | Original (P0–P5) | Amendment (P6–P10) | Total |
|---|---|---|---|
| **Codons** | 59 | 34 | **93** |
| **Source Files** | ~47 | ~5 (new handlers + routes) | **~52** |
| **Phases** | 6 (0–5) | 5 (6–10) | **11** |
| **Gateway Routes** | ~40 | +21 | **~61** |
| **UDS Bridge Methods** | 18 | +21 | **39** |

---

**🧬 END OF DNA STRAND AMENDMENT**

> *"The organism evolved beyond the original blueprint. This amendment ensures
> every clone gets the full genome."*
