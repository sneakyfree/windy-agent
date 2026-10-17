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
- [Offline](#offline)
- [Events & Observability](#events--observability)
- [Channels (SMS, Email)](#channels)
- [Soul Passport (Import/Export)](#soul-passport)
- [Providers (LLM Configuration)](#providers)
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

Get all current slider values (17 sliders, 0–10 scale).

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
    "adaptive_mode": 8
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

Skills are **text playbooks** the agent reads (numbered steps, exact
commands that worked). Nothing ever executes skill text. Code runs only
through the agent's own tools (e.g. `shell.exec` in its sandbox) under
the owner's trust settings. The old `evaluate`, `golden-tests` and
`regression` endpoints were removed on 2026-10-10 with executable skills.

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
      "name": "deploy-website",
      "version": 3,
      "language": "playbook",
      "promoted": true,
      "usage_count": 42
    }
  ]
}
```

Rows created before 2026-10-10 may still show an older `language`
value (e.g. `python`); they are inert text.

### `POST /api/skills`

Create a new (unpromoted) text playbook. `language` defaults to
`"playbook"`; any other value is refused with HTTP 400.

**Request:**
```json
{
  "name": "summarize-article",
  "code": "1. Fetch the page.\n2. Pull the headline and three key points.",
  "language": "playbook",
  "description": "How I summarize an article"
}
```

**Response:**
```json
{ "skill_id": "uuid" }
```

### `POST /api/skills/:id/promote`

Promote a playbook into the agent's skill index (an owner choice; there
is no evaluator gate).

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

## Offline

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
`personality.change`, `personality_drift`, `offline.fallback`,
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

---

## Soul Passport (import from another AI)

No route takes a host path. The file is uploaded, and every later step uses the token the upload
returns: 192 random bits, single use, valid for 30 minutes, sent only in a JSON body. One import may
wait at a time. A body carrying `export_path` is refused with 400. All routes need the dashboard
password.

### `POST /api/soul/upload`

The export as the raw request body (`Content-Type: application/octet-stream`). The optional
`X-Filename` header holds the URI-encoded file name and is used only to tell `.zip` from `.json`.

Limits and failures:
- `Content-Length` is required (411 without it).
- Over 90 MB gets 413 before anything is written.
- The file is held in `$XDG_RUNTIME_DIR` or `/dev/shm`, mode 0600, and is wiped whatever the outcome.

**Response:** `{"token": "...", "preview": "🧬 Soul Preview ..."}` or `{"error": "..."}`

### `POST /api/soul/preview`

**Request:** `{"token": "..."}`. **Response:** `{"preview": "..."}` or `{"error": "..."}`. Nothing is
written.

### `POST /api/soul/import`

Approve: write the import. Sensitive items are stored flagged for review (`/soul review` in chat).

**Request:** `{"token": "..."}`

**Response:** `{"imported": 12, "flagged": 2, "skipped": 1, "tone": {"humor": 7}}` or
`{"error": "..."}`. The tone can be applied with `/soul apply` in chat within 30 minutes.

### `POST /api/soul/reject`

**Request:** `{"token": "..."}`. **Response:** `{"rejected": true}`. Nothing is kept, and the file is
wiped.

---

## Providers

Retired in Wave 3 (2026-10). The gateway has no provider routes and stores no provider keys.
Keys live only in the agent's private env file (`WINDY_ENV_FILE`, else the project `.env`): put them there or
run the setup wizard. Windy agents with a passport choose their model in Windy Mind.

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
