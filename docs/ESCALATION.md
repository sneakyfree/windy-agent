# Windy Fly Escalation Matrix

When to wake Grant. When to wait until morning. Distilled from the gauntlet's failure-mode catalog (Phase 5.v23) and the operational lessons in `RUNBOOK.md`.

## Severity tiers

| Tier | Definition | Response time | Who's notified |
|---|---|---|---|
| **P1** | Bot is unresponsive for all users for >5 min | < 15 min | Grant via Telegram + SMS (if bot-down means Telegram is the bottleneck) |
| **P2** | One feature broken, others work; or single-chat issue | < 24h | Grant via morning digest (Phase 7.3 cron) |
| **P3** | Cosmetic, low impact, observed-but-not-impacting | < 1 week | `~/.windy-stress/canary_journal.md` only |

## P1 triggers — wake Grant

- `windy-0.service` status = `failed` or `inactive` for >5 min
- Telegram `getUpdates` returns non-200 for >5 min
- Every reply for the last 10 turns has been the PR #209 dedicated-401 message → OAuth expired AND auto-recovery failed
- Disk has <500MB free on `/home` (next DB write will fail)
- DB locked for >15 min (no write activity completing)
- Memory leak: RSS growth >500MB in <1h

## P2 triggers — morning digest

- Single tool consistently failing (e.g., `web_search` rate-limited 24h)
- v19 slash audit returning any RED (new wiring bug)
- A scheduled timer (`windy-weekly-brief`, `windy-evening-recap`) hasn't fired in its window
- One channel adapter broken while others work
- `/status` shows degraded badge intermittently
- Lifeboat enters AND exits within an hour (transient flap)
- Spend trajectory >80% of daily cap with hours remaining

## P3 — log-and-move-on

- Single grandma-vocab regression in one reply (caught by Phase 8 jargon CI)
- Test flake on one run (not 5x consecutive)
- Slow-but-completes turn (>10s but <60s)
- Cosmetic /status formatting issue
- Comment-only or doc-only delta

## When to NEVER auto-act

- Pushing to `master` of any `sneakyfree/windy-*` repo
- Force-push or `--no-verify`
- Modifying any file in `~/.windy/` other than logs
- Calling `systemctl restart windy-0` more than 3 times in 1 hour without first surfacing the underlying error to Grant

## On-call protocol

1. **First 5 minutes:** check `/launch-readiness` and `systemctl --user status windy-0`. If green and active, downgrade severity.
2. **Next 10 minutes:** consult `RUNBOOK.md` for the specific failure mode. Try the documented recovery.
3. **Beyond 15 minutes:** if P1 still active, fire alert via whatever channel still works (SMS if Telegram is dead).

## Auto-action allow-list

Operations Kit Zero / Claude Code CAN do without explicit per-instance Grant approval (per `CLAUDE.md` standing authority 2026-04-26):

- Self-merge own PRs after self-review + test-green
- Direct-commit doc-only / lint-only / <20-line changes
- Roll back own merges via `git revert`
- Bypass pre-existing CI style failures (not functional)

What Kit Zero canNOT do without explicit per-incident approval:

- Refresh OAuth token (browser-mediated)
- Tag a release or push to master
- Modify lockbox files
- Add new external service credentials

## Useful escalation one-liners

```bash
# Is the bot alive?
systemctl --user is-active windy-0 && echo OK

# When was the last successful paid call?
grep -E "POST.*api.anthropic.com.*200" ~/.windy/windy-0.log | tail -1

# How long since last user message?
grep "Telegram heartbeat" ~/.windy/windy-0.log | tail -1

# Force a /status fetch from the live bot via systemd reload
systemctl --user reload-or-restart windy-0
```
