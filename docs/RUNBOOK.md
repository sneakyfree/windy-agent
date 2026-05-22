# Windy Fly Runbook

Operator quick-reference for the 25 known failure modes from Phase 5.v23 of the launch gauntlet. Each section: **detect → diagnose → recover**.

> **Source of truth for current state:** `/launch-readiness` in Telegram, or `~/.windy-stress/LAUNCH_GAUNTLET_DASHBOARD.md` for the full dashboard.

---

## Auth / lifecycle failures

### OAuth token expired — 401 "invalid x-api-key"

**Detect:**
- `/status` shows "Plan: invalid x-api-key" or degraded badge
- Bot replies start with "🔑 *Your API key looks invalid*"
- Log: `[httpx] HTTP/1.1 401 Unauthorized` from `api.anthropic.com`
- `is_permanent_auth_error()` classifier in `agent/resurrect.py:399`

**Diagnose:** Token in `~/.windy/windy-0.env` expired or rotated. Re-probe with `curl -H "x-api-key: $KEY" https://api.anthropic.com/v1/messages -d '{"model":"claude-haiku-4-5-20251001","max_tokens":3,"messages":[{"role":"user","content":"hi"}]}'`.

**Recover:** Refresh per [`reference_anthropic_oauth_swap.md`](../../.claude/.../reference_anthropic_oauth_swap.md). Two-place procedure: `~/.windy/windy-0.env` + systemd-env (or wrapper plist). Restart `systemctl --user restart windy-0`.

**Recurrence cadence (per memory):** ~daily; investigate auto-refresh.

---

### Lifeboat wedged — Ollama keeps timing out

**Detect:**
- Log: `lifeboat wedged (3 consecutive Ollama failures) — clearing flag and retrying paid`
- `/lifeboat` shows ACTIVE state for >30 min
- Replies prefixed with 🛟

**Diagnose:** `ollama list` to see installed models. `curl http://localhost:11434/api/tags` to check service.

**Recover:** Either (a) refresh OAuth so paid path returns, OR (b) install a smaller Ollama model: `ollama pull llama3.2:3b`, OR (c) `/normal` to force exit from lifeboat.

---

### Bot service down / Telegram polling stopped

**Detect:**
- No 200 OK to `/getUpdates` in last 5 min in `~/.windy/windy-0.log`
- `systemctl --user status windy-0` shows inactive
- Liveness probe alert

**Diagnose:** Last lines of `~/.windy/windy-0.log`. Common causes: OOM kill, env file corruption, perma-auth wedge.

**Recover:** `systemctl --user restart windy-0`. If crash loop, check `journalctl --user -u windy-0 -n 200`.

---

## Database / disk failures

### DB locked

**Detect:** Log: `database is locked`. /status fact count shows "Could not query database".

**Diagnose:** Concurrent writer or stale lock. `lsof data/windyfly.db`.

**Recover:** Restart bot. If persistent, copy DB aside, restore from backup.

### Disk full

**Detect:** `df -h /home`. Bot writes fail. Liveness probe alerts.

**Recover:** `du -sh ~/.windy/* | sort -h` to find offender; usually `windy-0.log` (rotate it). InstaBio data/audio/ is voice-clone training — DO NOT delete (per memory).

---

## Network / external service failures

### Anthropic rate-limit (429)

**Detect:** Log `HTTP/1.1 429`. /status budget shows high spend.

**Diagnose:** Check `/budget` to see spend trajectory.

**Recover:** Bot's chain-fallback handles transient 429; wait it out. If persistent, lower DEFAULT_MODEL to Haiku, or cap with `/memory 200K` to reduce tokens/turn.

### Telegram-banned

**Detect:** Log: `Forbidden: bot was blocked by the user` or `429 Too Many Requests` from Telegram API.

**Recover:** Per-chat issue usually; bot continues serving other chats. If global, contact Telegram BotFather.

---

## Tool-level failures

### Tool timeout

**Detect:** `/status` last tool error. v22 stress harness reports.

**Recover:** Tool-specific. Check capability source under `src/windyfly/agent/capabilities/`.

### shell.exec denied / no PTY

**Detect:** Tool reply: "shell execution refused".

**Recover:** Check polkit rule (per memory: `/etc/polkit-1/rules.d/49-windy-install-deps.rules`).

### web_search hit daily cap

**Detect:** Tool reply: "WEB_SEARCH_UNAVAILABLE" or similar.

**Recover:** Wait until midnight UTC for daily cap reset, or bump cap in config.

---

## Channel-specific failures

### Voice transcription down

**Detect:** Voice messages return text-only error. Log: `piper` or `whisper` errors.

**Recover:** Check Ollama is running + voice model is installed.

### SMS/Mail send failures

**Detect:** `/sms` or `/send-mail` returns error. Check Twilio / Resend dashboards.

**Recover:** Validate `TWILIO_*` and `RESEND_API_KEY` env vars.

---

## Self-knowledge / confabulation failures

### Bot claims wrong HOST

**Detect:** Bot says it's running on a different machine. PR #188 tripwire should fire.

**Diagnose:** Check RUNTIME CONTEXT block in current prompt — does it reflect actual env?

**Recover:** Restart bot to refresh env detection.

### Bot impersonates Kit 0

**Detect:** Bot uses "I'll ssh into..." or "let me check on the VPS". PR #162 tripwire fires.

**Recover:** Tripwire retries with stronger prompt; if persistent, review prompt changes.

---

## Escalation

| Severity | Action |
|---|---|
| **P1** — bot unresponsive, all chats affected | Restart bot. If repeated, file issue. |
| **P2** — one feature broken, others work | Add to backlog, fix at next iteration. |
| **P3** — cosmetic, low impact | Track in `~/.windy-stress/canary_journal.md`. |

For repeat P1 incidents: review `~/.claude/.../memory/MEMORY.md` for known recurring issues + escalate to Grant via Telegram.

---

## Useful one-liners

```bash
# Refresh dashboard
python "/home/grantwhitmer/Desktop/Grant's Folder/windy-agent/scripts/render_gauntlet_dashboard.py"

# Run all no-OAuth stress stubs
bash ~/.windy-stress/run_gauntlet_stubs.sh

# Probe Anthropic auth
KEY=$(grep ^ANTHROPIC_API_KEY ~/.windy/windy-0.env | cut -d= -f2-) && \
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    https://api.anthropic.com/v1/messages \
    -d '{"model":"claude-haiku-4-5-20251001","max_tokens":3,"messages":[{"role":"user","content":"hi"}]}'

# Check live bot logs
tail -f ~/.windy/windy-0.log

# Restart bot
systemctl --user restart windy-0

# v19 slash audit (zero LLM cost)
python ~/.windy-stress/stress_v19_slash_audit.py
```
