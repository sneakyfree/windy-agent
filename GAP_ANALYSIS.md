# DNA Strand Gap Analysis

## 8 Key Questions

### 1. Multiple LLM providers?
**YES — IMPLEMENTED.** `models.py` routes to OpenAI-compatible and Anthropic-native SDKs. `providers.py` defines 12 built-in providers (OpenAI, Anthropic, Grok, Gemini, DeepSeek, Mistral, OpenRouter, Together, Groq, Perplexity, Fireworks, Ollama). Model routing works via exact match, prefix match, and active provider config.

### 2. Skill sandbox restricts dangerous operations?
**PARTIALLY.** Subprocess sandbox restricts PATH and cwd to `/tmp`. Evaluator has BANNED_PATTERNS regex blocklist (import os, subprocess, exec, eval, etc.). But no Docker/seccomp/OS-level isolation — regex blocklist is trivially bypassable. Matches spec intent for v1.

### 3. Cognitive decay runs on schedule?
**YES.** `main.py` starts a `_start_decay_scheduler()` daemon thread running `run_decay()` every 24 hours. Also manually triggerable via API.

### 4. Personality versioning detects drift?
**YES, but not on schedule.** `detect_drift()` compares current sliders against snapshot, flags >2 point changes. `run_periodic_drift_check()` exists but is NOT called by any scheduler. Only runs on-demand via API.

### 5. "Never Wrong Twice" injects correction skills?
**PARTIALLY.** Detects friction via regex, logs failures, injects correction prompts. But `correction_skill_id` column in failures table stays NULL — never creates actual correction skills.

### 6. Cost tracker enforces daily budget?
**YES — REAL ENFORCEMENT.** `loop.py` calls `check_budget()` before every LLM call. If over budget, returns a message and does NOT make the LLM call.

### 7. Sub-agent system spawns sub-agents?
**YES.** `sub_agents.py` contains `spawn_sub_agent()` with isolated context, token budget, and cost logging. Registered as `delegate_to_specialist` tool. Depth-limited to 1 (sub-agents don't get tool registry).

### 8. Offline queue flushes on reconnect?
**PARTIALLY.** `offline.py` has `queue_message()`, `replay_queued_messages()`, and atomic file writes to `data/offline_queue.json`. But `replay_queued_messages()` is never called automatically — no reconnect listener triggers it.

---

## Phase Scorecard

| Phase | Codons | Implemented | Partial | Missing | Score |
|-------|--------|-------------|---------|---------|-------|
| 0 — Agent Loop | 12 | 12 | 0 | 0 | 100% |
| 1 — Matrix Bot | 10 | 10 | 0 | 0 | 100% |
| 2 — Soul + Control Panel | 13 | 13 | 0 | 0 | 100% |
| 3 — Skills + Cost + Intents | 14 | 14 | 0 | 0 | 100% |
| 4 — Gateway + Advanced | 10 | 9 | 1 | 0 | 95% |
| 5 — Dashboard + Observability | 8 | 7 | 1 | 0 | 94% |
| 6 — Shape-Shift | 5 | 5 | 0 | 0 | 100% |
| 7 — Provider Management | 8 | 8 | 0 | 0 | 100% |
| 8 — Mission Control | 5 | 5 | 0 | 0 | 100% |
| 9 — Extended AI | 4 | 4 | 0 | 0 | 100% |
| 10 — API Surface | 12 | 12 | 0 | 0 | 100% |
| **TOTAL** | **101** | **99** | **2** | **0** | **~98%** |

---

## The 2 Partially Implemented Codons

### C4.10 — Offline queue flush on reconnect
- `replay_queued_messages()` exists with correct implementation
- Never called automatically — no reconnect event wires to it
- Messages persist to disk but sit there until manual API call

### C5.3 — Drift detection scheduled
- `detect_drift()` and `run_periodic_drift_check()` both implemented
- Decay scheduler only calls `run_decay()`, not `run_periodic_drift_check()`
- Drift detection is API-only, not on 24-hour schedule

---

## Additional Gaps (Not Codons)

1. **correction_skill_id always NULL** — failures table column exists but Never Wrong Twice doesn't generate correction skills
2. **Write queue batching** — spec says "batch up to 50 in one transaction" but implementation executes items individually
3. **FTS5 content-sync mode has no triggers** — `episodes_fts` uses content-sync but no INSERT/UPDATE/DELETE triggers exist, requiring manual `rebuild` for FTS search to find new episodes
4. **GET /api/cost/monthly** — not implemented anywhere (gateway, IPC, or Python)
5. **Node.js sandbox has no restricted environment** — Python sandbox restricts PATH/cwd, but Node sandbox runs with full environment
