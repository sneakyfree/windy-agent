# Overnight Stress + Auto-Repair Run — Instructions for the Autonomous Cron Worker

**Created 2026-05-10 by pre-compaction Opus 4.7 (1M context).**
**Read this file in FULL on every cron wake-up.** It is the source of truth.

---

## Mission

Run 200 stress prompts at the Windy 0 Telegram agent (`agent_respond()`
direct calls, not Telegram round-trips), detect failure patterns from
logs/events/responses, and SHIP code fixes through the night via the
standing autonomous-maintainer authority documented in
`/home/grantwhitmer/Desktop/Grant's Folder/windy-agent/CLAUDE.md`
(see "2026-04-26 — Kit Zero acting as autonomous maintainer
(standing authority)").

Grant is asleep. He'll wake at ~7 AM and read
`~/.windy-stress/run_*/morning_brief.md`. The job is to maximize
the value of those sleeping hours: ship real fixes for real failure
patterns, not noise.

---

## Architecture

Four pieces, all in this directory:

| File | Role |
|---|---|
| `corpus.py` | 200 prompts in 7 categories (Q&A, conversational, research, memory probe, slash commands, tool tasks, adversarial). |
| `run.py` | Harness. Calls `agent_respond()` per prompt. Writes one JSONL line per turn to `findings.jsonl`. Restartable, cost-capped, honors STOP file. |
| `analyze.py` | Reads `findings.jsonl` + the bot's `events` table + `~/.windy/windy-0.log`. Categorizes failures by frequency × severity. Writes `morning_brief.md`. |
| `start.sh` | One-shot launcher. Bumps env (`WINDY_DAILY_SEARCH_CAP=500`), creates run dir, starts `run.py` via `nohup`, prints pid + log path. |

State lives at `~/.windy-stress/`:
- `~/.windy-stress/run_TIMESTAMP/` — one dir per run
- `~/.windy-stress/run_TIMESTAMP/findings.jsonl` — harness output
- `~/.windy-stress/run_TIMESTAMP/morning_brief.md` — analyzer output
- `~/.windy-stress/run_TIMESTAMP/stress.db` — separate stress DB so prod isn't polluted
- `~/.windy-stress/journal.md` — per-cron-cycle journal (append-only)
- `~/.windy-stress/STOP` — if exists, halt everything
- `~/.windy-stress/PAUSE_REPAIR` — if exists, skip repair this cycle (harness keeps running)
- `~/.windy-stress/CURRENT_RUN` — symlink to the active run dir

---

## Standing Authority (CONFIRMED for tonight)

Per CLAUDE.md 2026-04-26 mandate + Grant's explicit "GO" for this run:

1. **Self-merge own PRs** after diff-sanity + full local test suite green using `gh pr merge --squash --admin --delete-branch`.
2. **Bypass pre-existing CI noise** only if style-only (ruff F, mypy strict). Functional test failure = abort.
3. **Direct-commit to master** for lint-debt < 20 lines that doesn't touch agent loop / capability handlers / channel adapters. Tonight: PREFER PR-with-self-merge route for traceability since the journal needs concrete PR numbers.
4. **Restart `windy-0.service`** after each merge.
5. **Roll back own merges** (`git revert`) freely if smoke probe reveals regression.

Tonight-specific guardrails (do NOT exceed):
- **At most ONE PR per cron wake-up.** No runaway shipping.
- **Hard cost cap $15 total for the night** (check via DB cost_log; halt if exceeded).
- **Halt all repair if `~/.windy-stress/STOP` exists.**
- **No edits to: anything outside `windy-agent`, system files, the bot's env file, the cron schedule itself.**

---

## Per-Cycle Workflow (run on EVERY cron wake-up)

```
0. STOP CHECK
   if ~/.windy-stress/STOP exists → write a final journal entry "halted",
   then exit. No fixes, no probes, no cron rescheduling.

1. CONTEXT REFRESH
   - Read this file (INSTRUCTIONS.md) again — it's the SoT.
   - cat ~/.windy-stress/journal.md (last ~50 lines) — what prior cycles did.
   - cd to /home/grantwhitmer/Desktop/Grant's Folder/windy-agent
   - git status (should be clean) ; git log --oneline -10 (overnight PRs landed)

2. HARNESS HEALTH
   - Find the active run via: readlink ~/.windy-stress/CURRENT_RUN
   - Last harness line: tail -5 ~/.windy-stress/run_*/findings.jsonl
   - If harness has been silent > 5 min:
     * Check ~/.windy-stress/run_*/harness.log for crash
     * If crashed, restart via nohup python run.py --resume &
     * Log incident in journal

3. ANALYZE
   - Run: python /home/grantwhitmer/Desktop/Grant's\ Folder/windy-agent/scripts/night_stress/analyze.py
   - This writes/updates morning_brief.md.
   - Read the top-3 ranked patterns from morning_brief.md.
   - Filter out: patterns already resolved this run (read FIXED.md if exists),
     patterns that already have an open PR by you tonight.

4. DECIDE
   - Pick the TOP unresolved pattern by impact (count × severity).
   - If top pattern has impact score < 3, skip repair this cycle (write
     journal "no high-impact patterns") and exit.
   - If PAUSE_REPAIR file exists, skip repair, log it, exit.

5. DIAGNOSE
   - Read the failure samples from morning_brief.md.
   - grep/Read relevant code files (loop.py, capabilities/*, tools/*, etc.)
   - Check the events table for related entries:
     sqlite3 [stress.db OR prod db] "SELECT event_type, data FROM events
       WHERE created_at > datetime('now', '-1 hour') ORDER BY id DESC LIMIT 20"
   - Identify the root cause. WRITE IT DOWN in the journal BEFORE coding.

6. FIX
   - git checkout -b fix/<descriptive-kebab>
   - Edit specific files. NO speculative refactors. Each change must
     map to a specific failure sample.
   - Run tests: cd .../windy-agent && .venv/bin/python -m pytest
   - REQUIRE >= 2620 tests passing AND no NEW failures vs. master baseline.
     Pre-existing flakes (test_telegram_hardening::test_B_three_round_tool_loop
     and test_agent_loop::TestToolExecution::test_tools_passed_to_llm)
     are OK — they reproduce on master.
   - If test count drops or new failures appear, ABORT: git checkout master,
     git branch -D <branch>, journal the abort reason, exit.

7. SHIP
   - git add <specific files> (NOT -A unless you double-checked)
   - git commit -m "$(cat <<'EOF'
     <commit message>

     Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
     EOF
     )"
   - git push -u origin <branch>
   - gh pr create --title "..." --body "..." (Test plan checkbox checked)
   - gh pr merge <num> --squash --admin --delete-branch
   - git checkout master && git pull --ff-only

8. DEPLOY + SMOKE PROBE
   - systemctl --user restart windy-0
   - sleep 6
   - Smoke probe: cd .../windy-agent && .venv/bin/python -c "
       import os; from pathlib import Path
       env = Path.home() / '.windy' / 'windy-0.env'
       for line in env.read_text().splitlines():
           if '=' in line and not line.strip().startswith('#'):
               k, v = line.split('=', 1)
               os.environ[k.strip()] = v.strip().strip('\"').strip(\"'\")
       import time
       from windyfly.memory.database import Database
       from windyfly.memory.write_queue import WriteQueue
       from windyfly.agent.loop import agent_respond
       from windyfly.config import load_config
       cfg = load_config('/home/grantwhitmer/.local/share/windyfly/soul/config.toml')
       db = Database(':memory:')
       from windyfly.memory.episodes import save_episode
       save_episode(db, 'user', 'bootstrap', session_id='bootstrap')
       wq = WriteQueue(); wq.start()
       t0 = time.time()
       resp = agent_respond(cfg, db, wq, 'hi, are you healthy?',
                            'smoke-probe-CYCLE_ID')
       el = time.time() - t0
       assert resp and len(resp.strip()) > 5, f'empty response: {resp!r}'
       assert el < 30, f'timeout: {el}s'
       print(f'OK ({el:.1f}s, {len(resp)} chars): {resp[:120]}')
       wq.stop(); db.close()
       "
   - If smoke FAILS:
     * git revert HEAD --no-edit && git push
     * systemctl --user restart windy-0 && sleep 6
     * Run smoke probe AGAIN. If still fails → severe incident, write
       INCIDENT.md with full diagnostics and STOP file, exit.
     * If second probe OK → log "reverted, healthy" in journal.

9. JOURNAL
   - Append to ~/.windy-stress/journal.md:
     ```
     ## CYCLE <wake_id> @ <iso timestamp>
     - Top pattern: <name> (count=<n>, severity=<s>)
     - Diagnosis: <root cause>
     - Action: shipped PR #<num> / no-op / reverted
     - Smoke probe: OK <elapsed>s / FAIL → reverted / N/A
     - Bot status: active <uptime>
     - Cost so far: $<usd>
     - Time spent: <minutes>
     ```

10. NEXT CYCLE
    - Cron already scheduled (we set it up at launch). Do NOT
      re-schedule unless the original schedule was disturbed.
    - exit.

```

---

## Cost Discipline

- Bot's prod model: `claude-sonnet-4-6` (Anthropic). Per-turn ~$0.015.
- 200 prompts × $0.015 = ~$3 base. Tier 0 web_search adds ~$1.50.
- Each repair cycle has its OWN cost (LLM-driven diagnosis). Budget
  ~$0.50 per cycle × 6-8 cycles overnight = $3-4.
- TOTAL EXPECTED: $7-8 for the night.
- HARD CAP: $15. Check via:
  ```sql
  SELECT SUM(cost_usd) FROM cost_log WHERE created_at > '<start_iso>';
  ```
- If exceeded, write STOP file and journal "cost cap hit".

---

## Failure Detection — What to Look For

The analyzer ranks these (severity in parens):

| Pattern | Detection signal | Severity |
|---|---|---|
| LLM call exception | `errors[]` in finding contains RuntimeError | 5 |
| Empty response | response_text strips to "" | 5 |
| Confabulation | events: `agent.confabulation_detected` (stage=initial or retry) | 4 |
| Self-env confab | events: `agent.confabulation_detected` (stage=self_env_*) | 4 |
| Write-intent unexecuted | events: `agent.write_intent_unexecuted` | 3 |
| Tool 5xx error | log line matches "HTTP 5xx" from tool dispatch | 3 |
| Tool timeout | tool call duration > 25s | 3 |
| Lifeboat triggered | events: `auto_resurrect.fired` | 4 |
| Lifeboat probe failed | events: `lifeboat.recovery_failed` (reason != cooldown) | 2 |
| Slow turn | total turn duration > 60s | 2 |
| Native search unsupported | events: `web_search.native_unsupported` | 3 |
| Native search cap reached | events: `web_search.native_skipped` reason=cap_reached | 1 |
| Recovery hint missing | response is offline-mode error + no /reset or /resurrect | 3 |

**Impact score = severity × log(1 + count).** Top score = best target.

---

## What "DONE" looks like at 7 AM

`~/.windy-stress/run_*/morning_brief.md` should contain:

```
# Overnight Stress + Repair Report — <start ISO> to <now ISO>

## TL;DR
- Prompts run: N / 200
- PRs shipped: N (links)
- Pre-existing pass count: 2620 → final: <N> (delta: +N)
- Cost: $X
- Bot uptime: X%
- Open patterns: N

## PRs shipped
1. PR #166 — <title> (closed pattern: <name>, count=<n>)
2. PR #167 — ...

## Failure patterns (after fixes applied)
- <pattern>: count <before> → <after>
- ...

## Still open at sunrise
- <pattern>: count <n>. Notes: <why not fixed — too risky / out of cycles>

## Incidents
- <if any>

## Journal excerpt (last 20 lines)
<tail>
```

---

## Anti-Patterns (don't do these)

- DO NOT bump test count by adding trivial tests just to pass the gate
- DO NOT push to master directly tonight (PR-with-self-merge gives traceable journal)
- DO NOT touch the env file `/home/grantwhitmer/.windy/windy-0.env`
- DO NOT touch the systemd unit file
- DO NOT cancel or modify the cron schedule (only halt via STOP file)
- DO NOT respond to the harness's own messages (the bot will, you stay silent)
- DO NOT batch multiple fixes into one cycle — one PR per wake-up, sized appropriately
- DO NOT chase pre-existing flakes (test_B_three_round_tool_loop, test_tools_passed_to_llm)
- DO NOT delete the stress run dir on failure — preserve evidence

---

## Reference: PRs shipped today (pattern match the rhythm)

- #160 — lifeboat stuck-state recovery (4-fix bundle)
- #161 — lifeboat: paid-key probe + post-recovery grace + /lifeboat + state events
- #162 — self-env confab guard (RUNTIME GUARDRAIL + tripwire)
- #163 — fetch_url failover (windy-search 502 → direct httpx)
- #164 — Tier 0 Anthropic native web_search
- #165 — tool rounds 3→5 + write-intent tripwire

These are good size, well-tested, surgical. Pattern-match the
commit message format, the PR body, and the test density.

---

## Final word

Be conservative when in doubt. Reverting a borderline fix is fine.
Shipping a broken fix at 4 AM is not. Grant trusts the maintainer
judgment more than the volume of work.
