# Windy Fly: telemetry and privacy

> **Nothing is sent until you've been told.** You see one line saying what's
> sent when your agent is born (at the end of the hatch) and at `windy login`
> / `windy go`. Only after that does an agent with a passport send the health
> data below, signed with its own passport token. Turn it off any time with
> `windy telemetry off` or `WINDY_TELEMETRY=0`.

Windy Fly runs on your machine. To see when agents fail in the field, it
sends a small amount of **anonymous health data** to Windy Admin
(`admin.windyword.ai`). This page lists exactly what.

## The rule

Codes, counts, ids and durations only. **Never** message content, prompts,
tool arguments, file contents, names or email addresses. Metadata keys that
look like content are refused by the client before sending, and again by the
server.

## Seeing and changing the setting

- `windy telemetry status`: what is sent, which credential would be used,
  whether you've been told, and whether sending is paused.
- `windy telemetry off` / `windy telemetry on`: the switch (saved in
  `~/.windy`). `WINDY_TELEMETRY=0` / `=1` in the environment wins over it.

An agent that only ever runs as a background service, where nobody has run
an interactive `windy` command, sends nothing until someone runs
`windy telemetry status` (or `on`), or sets `WINDY_TELEMETRY=1`.

## What is sent

| Event | When | Fields |
|---|---|---|
| `service.boot` | once per process | version, install (`pip` / `checkout`), channel_count |
| `service.health` | every 15 min | interval_s, turns, turn_errors, tool_calls, tool_errors, retries_429, p95_turn_ms, lifeboat_turns, degraded, telemetry_quarantined, telemetry_dropped |
| `agent.run_failed` | a turn where you got no real answer | code (e.g. `rate_limited`, `auth`, `lifeboat`), stage, http_status, channel, model, duration_ms |
| `agent.model_demoted` | when the agent falls back to a weaker/local model (once per switch) | from_model, to_model, reason |
| `llm.call` | each model API call | model, provider, tokens in/out, cache tokens, list-price cost, billing |

Every row carries your agent's passport number (its public Eternitas id). A
count the agent can't know is left out rather than sent as zero.

## Credentials, and when Windy refuses them

Rows are signed with, in order: a Windy fleet operator token
(`WINDY_ADMIN_INGEST_TOKEN`, Windy's own machines), a Windy Fly client token
(`WINDY_TELEMETRY_CLIENT_TOKEN`, empty by default; none is built into the
package), or, the normal case, your agent's own Eternitas passport token.
Windy's ingest checks that token and only files rows under that same
passport. `WINDY_TELEMETRY_EPT_AUTH=0` stops the passport path; an agent
with no passport sends nothing.

Rows are batched: at most one request every ~10 seconds (100 rows each),
plus a last flush when the agent exits.

If the ingest says the passport token has expired, the agent renews it once
and tries again. If Windy still refuses it (or the passport is revoked), the
agent stops sending for the rest of the run and for 24 hours after (a marker
file in `~/.windy`), logs one warning, and doesn't retry in between. If the
ingest says "slow down", the agent waits as asked and keeps a bounded queue;
the oldest rows are dropped if it fills.

## Test traffic

Windy's own probes set `WINDY_SYNTHETIC=1`. Their rows are marked
`synthetic`, and their requests to Windy services (and only Windy services) carry the
`X-Windy-Synthetic: 1` header so dashboards can separate test traffic from
yours. It is never set on real use.
