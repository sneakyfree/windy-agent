# Windy Fly API Reference

> Complete reference for all gateway endpoints exposed by the Windy Fly platform.
> Gateway runs on `http://localhost:3000` by default.

---

## Table of Contents

- [Health & Status](#health--status)
- [Control Panel (Sliders)](#control-panel-sliders)
- [Memory](#memory)
- [Skills Management](#skills-management)
- [Personality (History, Snapshots, Drift, Rollback)](#personality)
- [Cost Tracking](#cost-tracking)
- [Conflicts](#conflicts)
- [Cognitive Decay](#cognitive-decay)
- [Mode & Offline](#mode--offline)
- [Events & Observability](#events--observability)
- [Channels (SMS, Email)](#channels)
- [Soul Passport (Import/Export)](#soul-passport)
- [Shape-Shifting](#shape-shifting)
- [Providers (LLM Configuration)](#providers)
- [Mission Control (Machines)](#mission-control-machines)
- [Setup Wizard](#setup-wizard)
- [WebSocket Endpoints](#websocket-endpoints)

---

## Health & Status

### `GET /api/health`

Health check for the gateway and brain connection.

**Response:**
```json
{
  "status": "ok",
  "brain_connected": true,
  "timestamp": "2026-03-31T10:00:00.000Z"
}
```

### `GET /api/dashboard`

Comprehensive dashboard summary including memory, costs, failures, skills, intents, and personality.

**Response:**
```json
{
  "dashboard": {
    "memory": {
      "total_nodes": 47,
      "total_episodes": 312,
      "by_scope": { "personal": 35, "work": 12 }
    },
    "costs": {
      "today_usd": 0.43,
      "this_week_usd": 2.15,
      "this_month_usd": 8.72
    },
    "failures": {
      "unresolved": 2,
      "resolved": 15,
      "improvement_rate": 0.88
    },
    "skills": {
      "total": 5,
      "promoted": 3,
      "top_5_by_usage": []
    },
    "intents": {
      "active": 3,
      "completed": 12,
      "abandoned": 1
    },
    "personality": {
      "sliders": { "humor": 7, "warmth": 9 },
      "preset": "buddy",
      "estimated_monthly_cost": 12.50
    }
  }
}
```

---

## Control Panel (Sliders)

### `GET /api/sliders`

Get all current slider values (18 sliders, 0–10 scale).

**Response:**
```json
{
  "sliders": {
    "personality": 8,
    "humor": 7,
    "formality": 4,
    "reasoning_depth": 5,
    "creativity": 6,
    "memory_depth": 6,
    "context_window": 5,
    "proactivity": 7,
    "autonomy": 4,
    "verbosity": 6,
    "response_length": 5,
    "epistemic_strictness": 4,
    "tool_reloop_rounds": 2,
    "emotional_sensitivity": 6,
    "memory_retention": 6,
    "warmth": 7,
    "adaptive_mode": 8,
    "shape_shift_bias": 8
  }
}
```

**Offline fallback:** Returns all sliders at 5 with `_offline: true`.

### `GET /api/sliders/info`

Get slider metadata including labels, descriptions, impact descriptions, current values, and cost-per-point.

**Response:**
```json
{
  "sliders": {
    "personality": {
      "label": "Personality",
      "description": "How much warmth, character, and soul the agent puts into responses.",
      "impact_low": "Robotic, clinical responses. Zero flair.",
      "impact_high": "Full SOUL.md personality, warm, human-like.",
      "value": 8,
      "cost_per_point": 0.3
    }
  }
}
```

### `PUT /api/sliders/:name`

Set an individual slider value.

**Request:**
```json
{ "value": 7 }
```

**Response:**
```json
{ "success": true }
```

**Errors:**
- `400` — Invalid slider name or value (must be 0–10)
- `503` — Brain offline

---

## Memory

### `GET /api/memory/search?q=<query>&limit=<n>`

Search knowledge nodes by query string.

| Parameter | Type   | Default | Description           |
|-----------|--------|---------|-----------------------|
| `q`       | string | `""`    | Search query          |
| `limit`   | int    | `10`    | Max results to return |

**Response:**
```json
{
  "nodes": [
    {
      "id": "uuid",
      "type": "fact",
      "name": "Favorite color is blue",
      "confidence": 0.95,
      "created_at": "2026-03-15T10:00:00"
    }
  ]
}
```

### `GET /api/intents`

List active intents (pending user goals the agent is tracking).

**Response:**
```json
{
  "intents": [
    {
      "id": "uuid",
      "description": "Find a good Italian restaurant nearby",
      "status": "active",
      "priority": 7,
      "created_at": "2026-03-30T14:00:00"
    }
  ]
}
```

### `GET /api/moments?limit=<n>`

List relationship moments (significant interactions).

**Response:**
```json
{
  "moments": [
    {
      "summary": "User shared excitement about new job",
      "emotional_context": "excited",
      "session_id": "uuid",
      "created_at": "2026-03-28T15:30:00"
    }
  ]
}
```

### `GET /api/failures?limit=<n>`

List failure events with root cause analysis.

**Response:**
```json
{
  "failures": [
    {
      "id": "uuid",
      "fault_type": "wrong_answer",
      "description": "Gave incorrect timezone for Tokyo",
      "root_cause": "Used outdated timezone offset",
      "correction_action": "Verify with web search",
      "improvement_verified": true,
      "created_at": "2026-03-25T09:00:00"
    }
  ]
}
```

---

## Skills Management

### `GET /api/skills?promoted=<bool>`

List all skills or only promoted ones.

| Parameter  | Type | Default | Description                |
|------------|------|---------|----------------------------|
| `promoted` | bool | `false` | Only return promoted skills |

**Response:**
```json
{
  "skills": [
    {
      "id": "uuid",
      "name": "web_search",
      "version": 3,
      "language": "python",
      "promoted": true,
      "usage_count": 42,
      "eval_score": 0.95
    }
  ]
}
```

### `POST /api/skills`

Create a new skill.

**Request:**
```json
{
  "name": "summarize_article",
  "code": "def run(url):\n    ...",
  "language": "python"
}
```

**Response:**
```json
{ "skill_id": "uuid" }
```

### `POST /api/skills/:id/evaluate`

Run the evaluation suite on a skill.

**Response:**
```json
{
  "evaluation": {
    "score": 0.92,
    "passed": true,
    "results": { "test_1": "pass", "test_2": "pass" }
  }
}
```

### `POST /api/skills/:id/promote`

Promote a skill to production status.

**Response:**
```json
{ "promoted": true, "skill_id": "uuid" }
```

### `POST /api/skills/:id/rollback`

Rollback a skill to its parent version.

**Response:**
```json
{ "rolled_back": true, "skill_id": "uuid" }
```

### `POST /api/skills/:id/golden-tests`

Run golden tests for a specific skill.

**Response:**
```json
{
  "golden_tests": {
    "passed": 5,
    "failed": 0,
    "results": []
  }
}
```

### `POST /api/skills/regression`

Run the full regression suite across all promoted skills.

**Response:**
```json
{
  "regression": {
    "total_skills": 3,
    "total_tests": 15,
    "passed": 15,
    "failed": 0
  }
}
```

---

## Personality

### `GET /api/personality/history?limit=<n>`

Get recent personality change history.

| Parameter | Type | Default | Description      |
|-----------|------|---------|------------------|
| `limit`   | int  | `20`    | Max entries      |

**Response:**
```json
{
  "history": [
    {
      "id": "uuid",
      "soul_id": "uuid",
      "old_value": "5",
      "new_value": "7",
      "changed_by": "user",
      "created_at": "2026-03-30T10:00:00"
    }
  ]
}
```

### `POST /api/personality/snapshot`

Create a versioned checkpoint of the current personality.

**Request:**
```json
{ "changed_by": "user" }
```

**Response:**
```json
{ "batch_id": "uuid" }
```

### `GET /api/personality/drift`

Detect unauthorized personality drift (sliders that moved >2 points without user action).

**Response (no drift):**
```json
{ "drift": null }
```

**Response (drift detected):**
```json
{
  "drift": {
    "drifted_sliders": [
      { "name": "humor", "old": 7, "new": 3, "delta": 4 }
    ],
    "drift_source": "agent_evolution"
  }
}
```

### `POST /api/personality/rollback`

Rollback personality to a previous snapshot date.

**Request:**
```json
{ "snapshot_date": "2026-03-25T00:00:00" }
```

**Response:**
```json
{ "restored_count": 5 }
```

---

## Cost Tracking

### `GET /api/cost/daily`

Get today's spending total.

**Response:**
```json
{ "daily_spend": 2.43 }
```

**Offline fallback:** Returns `{ "daily_spend": 0, "_offline": true }`.

---

## Conflicts

### `GET /api/conflicts`

List unresolved memory conflicts.

**Response:**
```json
{
  "conflicts": [
    {
      "id": "uuid",
      "node_id": "uuid",
      "old_value": "Lives in NYC",
      "new_value": "Lives in LA",
      "resolution_status": "unresolved",
      "created_at": "2026-03-29T12:00:00"
    }
  ]
}
```

### `POST /api/conflicts/:id/resolve`

Resolve a memory conflict.

**Request:**
```json
{
  "resolution": "User confirmed they moved to LA",
  "keep_new": true
}
```

**Response:**
```json
{ "resolved": true, "conflict_id": "uuid" }
```

---

## Cognitive Decay

### `POST /api/decay/run`

Trigger a cognitive decay cycle (normally runs every 24 hours automatically).

**Response:**
```json
{
  "decay": {
    "nodes_decayed": 5,
    "episodes_decayed": 12,
    "intents_decayed": 1,
    "edges_decayed": 3
  }
}
```

---

## Mode & Offline

### `GET /api/mode`

Get the current agent mode.

**Response:**
```json
{ "mode": "companion" }
```

Valid modes: `companion`, `assistant`, `researcher`, `coder`, `creative`.

### `PUT /api/mode`

Set the agent mode.

**Request:**
```json
{ "mode": "researcher" }
```

**Response:**
```json
{ "mode": "researcher" }
```

### `GET /api/offline/status`

Check if the agent is online and whether Ollama (local LLM) is available.

**Response:**
```json
{
  "online": true,
  "ollama_available": false
}
```

---

## Events & Observability

### `GET /api/events?type=<event_type>&limit=<n>`

List recent events with optional type filter.

| Parameter | Type   | Default | Description        |
|-----------|--------|---------|--------------------|
| `type`    | string | all     | Filter by type     |
| `limit`   | int    | `50`    | Max events         |

**Known event types:**
`agent.respond`, `memory.write`, `skill.evaluate`, `cost.log`, `failure.detect`,
`intent.surface`, `conflict.detect`, `decay.run`, `matrix.message`, `matrix.reconnect`,
`personality.change`, `personality_drift`, `offline.fallback`, `sub_agent.spawn`,
`shape_shift.enter`, `shape_shift.exit`, `shape_shift.tool`, `shape_shift.restore`,
`sms.inbound`, `sms.outbound`, `sms.optout`, `email.inbound`, `email.outbound`

**Response:**
```json
{
  "events": [
    {
      "id": 42,
      "event_type": "agent.respond",
      "data": { "session_id": "uuid", "tokens": 150 },
      "created_at": "2026-03-31T10:00:00"
    }
  ],
  "counts_24h": {
    "agent.respond": 25,
    "memory.write": 12
  }
}
```

---

## Channels

### `POST /api/sms/webhook`

Twilio inbound SMS webhook. Processes the message through the agent and returns TwiML.

**Request (Twilio format):**
```json
{
  "From": "+15551234567",
  "Body": "Hey Windy, what's the weather?"
}
```

**Response (TwiML):**
```xml
<Response><Message>It's sunny and 72°F today! ☀️</Message></Response>
```

### `POST /api/sms/send`

Send an outbound SMS.

**Request:**
```json
{
  "to": "+15551234567",
  "message": "Hey! Just checking in. 🪰"
}
```

**Response:**
```json
{ "status": "sent", "sid": "SM..." }
```

### `POST /api/email/webhook`

SendGrid inbound email webhook. Parses email and generates a response.

**Request:**
```json
{
  "from": "user@example.com",
  "subject": "Question about my project",
  "text": "Can you help me plan..."
}
```

**Response:**
```json
{ "response": "Of course! Here's what I'd suggest..." }
```

### `POST /api/email/send`

Send an outbound email.

**Request:**
```json
{
  "to": "user@example.com",
  "subject": "Follow-up on your project",
  "body": "Here are the details we discussed..."
}
```

**Response:**
```json
{ "status": "sent" }
```

---

## Soul Passport

### `POST /api/soul/preview`

Preview a Soul Passport import without writing to the database.

**Request:**
```json
{
  "export_path": "/path/to/export.json",
  "source_type": "chatgpt"
}
```

**Response:**
```json
{
  "preview": "Found 42 facts, 15 preferences...",
  "stats": { "facts": 42, "preferences": 15 }
}
```

### `POST /api/soul/import`

Import a Soul Passport — parse and write to the knowledge graph.

**Request:**
```json
{
  "export_path": "/path/to/export.json",
  "source_type": "chatgpt"
}
```

**Response:**
```json
{
  "imported": true,
  "stats": { "facts": 42, "preferences": 15 },
  "preview": "..."
}
```

---

## Shape-Shifting

### `POST /api/shape-shift`

Shape-shift the agent into a specialist preset (in-place personality reconfiguration).

**Request:**
```json
{ "preset": "coder" }
```

**Response:**
```json
{
  "shifted_to": "coder",
  "announcement": "🔧 Switching to engineer mode...",
  "saved_sliders": { "humor": 7, "warmth": 9 },
  "applied": { "humor": 0, "warmth": 1 }
}
```

### `POST /api/shape-shift/restore`

Restore sliders to their pre-shift values.

**Request:**
```json
{
  "sliders": { "humor": 7, "warmth": 9 }
}
```

**Response:**
```json
{ "restored": true }
```

---

## Providers

### `GET /api/providers`

List all configured LLM providers and their status.

**Response:**
```json
{
  "providers": [
    {
      "key": "openai",
      "name": "OpenAI",
      "configured": true,
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  ]
}
```

### `POST /api/providers`

Add a custom provider.

**Request:**
```json
{
  "key": "custom_llm",
  "name": "My Custom LLM",
  "base_url": "https://api.custom.com/v1"
}
```

### `PUT /api/providers/:key`

Update a provider's configuration.

### `DELETE /api/providers/:key`

Remove a custom provider.

### `POST /api/providers/discover`

Discover available models from a provider's API.

**Request:**
```json
{ "provider": "openai" }
```

**Response:**
```json
{
  "provider": "openai",
  "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
  "fetched_at": "2026-03-31T10:00:00Z"
}
```

### `POST /api/providers/discover-all`

Discover models from all configured providers.

### `POST /api/providers/validate`

Validate that a provider's API key works.

**Request:**
```json
{ "provider": "openai" }
```

**Response:**
```json
{ "valid": true }
```

### `PUT /api/providers/active-model`

Set the active model for all conversations.

**Request:**
```json
{ "model": "gpt-4o" }
```

### `PUT /api/providers/set-key`

Set a provider's API key.

**Request:**
```json
{
  "key": "openai",
  "api_key": "sk-...",
  "api_key_env": "OPENAI_API_KEY"
}
```

### `POST /api/providers/keys`

Add a new API key (multi-key support).

**Request:**
```json
{
  "provider": "openai",
  "label": "Production key",
  "key": "sk-...",
  "type": "api"
}
```

### `DELETE /api/providers/keys`

Delete an API key.

### `PUT /api/providers/keys/activate`

Activate a specific key for a provider.

### `PUT /api/providers/notes`

Save notes for a provider.

### OAuth (OpenRouter)

- `POST /api/providers/oauth/openrouter/start` — Start OAuth flow
- `POST /api/providers/oauth/openrouter/callback` — Complete OAuth
- `GET /oauth/callback` — OAuth redirect handler

---

## Mission Control (Machines)

### `GET /api/machines`

List all registered remote machines with status.

### `POST /api/machines`

Register a new machine.

**Request:**
```json
{
  "name": "Production Server",
  "host": "192.168.1.100",
  "port": 3100,
  "token": "secret",
  "tags": ["production"],
  "notes": "Main deployment"
}
```

### `GET /api/machines/:id`

Get a single machine's status.

### `PUT /api/machines/:id`

Update machine configuration.

### `DELETE /api/machines/:id`

Remove a machine.

### `POST /api/machines/:id/restart-gateway`

Restart the gateway on a remote machine.

### `POST /api/machines/:id/restart-brain`

Restart the brain on a remote machine.

### `GET /api/machines/:id/health`

Get health status of a remote machine.

### `POST /api/machines/:id/exec`

Execute a command on a remote machine.

**Request:**
```json
{ "command": "uv run pytest tests/ -v" }
```

### `POST /api/machines/sync-providers`

Sync provider configurations to remote machines.

---

## Setup Wizard

> **Security:** All setup routes are restricted to localhost only (403 for remote requests).
> Rate limited to 10 requests/minute per IP.

### `GET /api/setup/status`

Check if Windy Fly is configured.

**Response:**
```json
{
  "configured": true,
  "existing_keys": ["OPENAI_API_KEY"]
}
```

### `POST /api/setup/validate-key`

Validate an API key against its provider.

**Request:**
```json
{
  "key_name": "OPENAI_API_KEY",
  "key_value": "sk-..."
}
```

**Response:**
```json
{ "valid": true }
```

### `POST /api/setup/finalize`

Write `.env` and `windyfly.toml` configuration files.

**Request:**
```json
{
  "api_keys": { "OPENAI_API_KEY": "sk-..." },
  "model": "gpt-4o-mini",
  "preset": "buddy"
}
```

**Response:**
```json
{ "ok": true }
```

### `POST /api/setup/launch`

Notify the brain to reload configuration after setup.

**Response:**
```json
{ "ok": true, "dashboard": "http://localhost:3000" }
```

---

## WebSocket Endpoints

### `WS /ws/chat`

Real-time chat with the agent over WebSocket.

**Send:**
```json
{
  "message": "What's on my calendar today?",
  "session_id": "uuid"
}
```

**Receive:**
```json
{
  "response": "You have 3 meetings today...",
  "session_id": "uuid"
}
```

### `WS /ws/terminal/:machineId`

Terminal relay to a remote machine (PTY).

### `WS /ws/machine/:machineId`

Event stream from a remote machine.

---

## Journal & Assessment

### `GET /api/journal`

List journal entries (agent's internal reflections).

**Response:**
```json
{
  "journal": [
    {
      "entry": "Today I learned the user prefers concise answers...",
      "created_at": "2026-03-30T22:00:00"
    }
  ]
}
```

### `POST /api/assessment`

Run an agent self-assessment.

**Response:**
```json
{
  "assessment": {
    "overall_score": 8.5,
    "strengths": ["Memory recall", "Tone matching"],
    "areas_for_improvement": ["Response length consistency"]
  }
}
```

---

## Error Handling

All endpoints return errors in a consistent format:

```json
{
  "error": "Description of what went wrong"
}
```

| Status | Meaning                              |
|--------|--------------------------------------|
| `200`  | Success                              |
| `400`  | Bad request (invalid input)          |
| `403`  | Forbidden (localhost-only routes)     |
| `404`  | Not found                            |
| `429`  | Rate limited                         |
| `500`  | Internal server error                |
| `502`  | Bad gateway (remote machine error)   |
| `503`  | Service unavailable (brain offline)  |
