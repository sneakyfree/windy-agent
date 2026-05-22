# LIFEBOAT_FSM_AS_BUILT — windy-agent Phase 2.2.1

**Status:** As-built documentation captured 2026-05-21 by the Plan agent during gauntlet Phase 2.2.1. The refactor target FSM in §5 is a *proposal* — do not implement before reviewing §5 design questions.

**Scope:** `agent/resurrect.py` (843 lines), `agent/offline.py` (457 lines), `agent/loop.py` §1.7 + chain-fail catch, `agent/models.py` cooldown layer, `channels/telegram_bot.py` `/normal` + panic handler, `channels/slash_commands.py` parsers.

---

## 1. States

The system is a *composition* of orthogonal flag axes, not a single-axis FSM. Implicit states from the as-built code:

| State | Predicate | File:line |
|---|---|---|
| **HEALTHY** | `not is_resurrected()` AND no provider has `_provider_cooldowns` entry | `resurrect.py:89`, `models.py:60-68` |
| **DEGRADED** (cooldown-only) | `not is_resurrected()` AND `_is_provider_in_cooldown(p)` for some p, but not all | `models.py:63-68` |
| **LIFEBOAT_HEALTHY** | `is_resurrected()` AND `consecutive_ollama_failures() < 3` AND not in recovery-probe cooldown | `resurrect.py:89`, `offline.py:345-359` |
| **LIFEBOAT_PROBING** | `is_resurrected()` AND `not _within_recovery_probe_cooldown()` (entering loop step 1.7) | `resurrect.py:636-645`, `loop.py:852-864` |
| **LIFEBOAT_WEDGED** | `is_resurrected()` AND `should_escape_lifeboat()` (≥3 consecutive Ollama failures) | `offline.py:356-359`, `loop.py:900-918` |
| **POST_RECOVERY_GRACE** | `not is_resurrected()` AND `_within_post_recovery_grace()` (5-min window) | `resurrect.py:518, 532-541` |
| **PERMA_AUTH_DEAD** | implicit — provider keys in long cooldown via `_COOLDOWN_AUTH_DEAD_S` (1h); is_perma_auth classifier hit | `resurrect.py:399-437`, `models.py:58, 87-95` |
| **AUTO_DISABLED** | `is_auto_resurrect_disabled()` (orthogonal user preference; overlays any other state) | `resurrect.py:331-337` |
| **AUTO_COOLDOWN** | `_within_auto_cooldown()` — orthogonal 60s gate on forward trip | `resurrect.py:373-385` |

**Count:** 6 substantive states + 3 orthogonal modifiers. The gauntlet's 5-state cap requires collapsing some of these (see §5).

---

## 2. Transitions

`A → B` with trigger, side effects, originating PR. Citations use `file:line`.

| # | A → B | Trigger | Side effects | PR |
|---|---|---|---|---|
| T1 | HEALTHY → LIFEBOAT_HEALTHY | User types `/resurrect`; Ollama present | Write `.resurrected` flag; warm model; clear Ollama-fail counter | #138 (`resurrect.py:178-260`) |
| T2 | HEALTHY → LIFEBOAT_HEALTHY | Chain exhaustion in `call_llm` + transient error + auto-on + not in cooldown/grace | Same as T1 + notification prepended | #145 (`resurrect.py:440-489`, `loop.py:1093-1128`) |
| T3 | HEALTHY → HEALTHY (auth-dead reply) | Chain exhaustion + `is_permanent_auth_error(msg)` | Dedicated "🔑 API key looks invalid" reply; log `auth.permanent_failure`; no flag write | #209 (`resurrect.py:471-476`, `loop.py:1145-1173`) |
| T4 | HEALTHY → DEGRADED | First non-perma-auth provider failure | `_provider_cooldowns[p] = (now + 30s*n, n)` | #46 baseline, refined by #210 (`models.py:71-100`) |
| T5 | HEALTHY → PERMA_AUTH_DEAD | Provider failure classified perma-auth | `_provider_cooldowns[p] = (now + 3600s, n)` — long cooldown bucket | #210 (`models.py:87-95`) |
| T6 | DEGRADED → HEALTHY | Provider success on any call | `_record_provider_success` deletes entry | #46 (`models.py:103-105`) |
| T7 | LIFEBOAT_HEALTHY → LIFEBOAT_PROBING | Next agent_respond turn + recovery cooldown expired | Stamp `_recovery_probe_marker_path`, run `_paid_health_probe` | #160 + #161 (`resurrect.py:657-688`, `loop.py:852-864`) |
| T8 | LIFEBOAT_PROBING → POST_RECOVERY_GRACE | `_paid_health_probe.ok == True` | `normalize()` deletes flag; stamp `_post_recovery_grace_path`; prepend "✅ Recovered" notice; log `lifeboat.exited` | #161 (`resurrect.py:690-719`, `loop.py:865-874`) |
| T9 | LIFEBOAT_PROBING → LIFEBOAT_HEALTHY | Probe fails (still_offline / no_keys / cooldown) | Log `lifeboat.recovery_failed`; route to Ollama | #161 (`resurrect.py:683-688`, `loop.py:876-883`) |
| T10 | LIFEBOAT_HEALTHY → LIFEBOAT_WEDGED | `_record_ollama_outcome(False)` brings counter ≥ 3 | Counter file ticks; `should_escape_lifeboat()` flips True | #201 (`offline.py:326-359`) |
| T11 | LIFEBOAT_WEDGED → HEALTHY | Next agent_respond turn; probe fails AND `should_escape_lifeboat()` True | `normalize()`; "⚠️ backup brain wasn't keeping up" notice; log `lifeboat.escaped_wedged`; fall through to paid path | #201 (`loop.py:900-918`) |
| T12 | LIFEBOAT_* → HEALTHY | User types `/normal` | `normalize()` deletes flag; success ack | #138 (`resurrect.py:263-281`, `telegram_bot.py:1549-1580`) |
| T13 | LIFEBOAT_* → HEALTHY | User types panic `/reset` | `normalize()` called; full process restart; pending greeting set | #160 fix-1 (`telegram_bot.py:1633-1663`) |
| T14 | POST_RECOVERY_GRACE → HEALTHY | 5 min elapsed (file mtime check) | None — implicit; marker file kept | #161 (`resurrect.py:518, 532-541`) |
| T15 | POST_RECOVERY_GRACE → blocked re-entry | Chain-fail during grace; auto_resurrect_attempt called | Short-circuit with `reason="post_recovery_grace"`; log `auto_resurrect.skipped`; fall through to offline_response | #161 (`resurrect.py:477-484`) |
| T16 | HEALTHY → HEALTHY (auto-cooldown reject) | Chain-fail within 60s of prior attempt | Short-circuit with `reason="cooldown"`; fall through to offline_response | #145 (`resurrect.py:485-486`) |
| T17 | LIFEBOAT_HEALTHY → queued | Ollama returns "Local model error" / "currently offline" text marker | `queue_message(user_message, session_id)` writes JSON queue | #160 missing → #166 (`loop.py:938-939`) |
| T18 | * → AUTO_DISABLED | User `/auto-resurrect off` | Write `.auto_resurrect_disabled` flag | #145 (`resurrect.py:340-370`) |
| T19 | AUTO_DISABLED → enabled | User `/auto-resurrect on` | Unlink flag | #145 (`resurrect.py:340-370`) |

**Edge case T17** was specifically the bug fixed by PR #166 (test_lifeboat_actually_queues.py).

---

## 3. Hidden State

Everything that influences behavior but isn't an obvious "state machine variable":

| Hidden | Type | Path / Var | Purpose | File:line |
|---|---|---|---|---|
| Resurrect flag | File | `~/.windy/.resurrected` (env: `WINDY_RESURRECT_FLAG`) | THE lifeboat-on bit + metadata JSON | `resurrect.py:82-86` |
| Auto-disable flag | File | `~/.windy/.auto_resurrect_disabled` | User opt-out | `resurrect.py:314-319` |
| Auto-attempt marker | File | `~/.windy/.auto_resurrect_last` (Unix ts) | 60s forward-trip cooldown | `resurrect.py:322-328` |
| Recovery-probe marker | File | `~/.windy/.recovery_probe_last` | 120s backward-trip cooldown | `resurrect.py:626-633` |
| Post-recovery grace marker | File | `~/.windy/.post_recovery_grace` | 5-min anti-ping-pong | `resurrect.py:521-529` |
| Ollama failure counter | File | `~/.windy/.ollama_fail_count` (int) | Wedged-escape trigger | `offline.py:319-322` |
| Offline message queue | File | `data/offline_queue.json` | Replay-on-recovery message store | `offline.py:366-369` |
| `_provider_cooldowns` | Module dict | `models.py:60` | per-provider circuit breaker (NOT shared across processes — DB-less) | `models.py:60-105` |
| `_ANTHROPIC_AUTH_PATH_LOGGED` | Module flag | `models.py:332` | Once-per-process telemetry latch | `models.py:332-355` |
| Constants | Module floats | `_AUTO_COOLDOWN_S=60`, `_RECOVERY_PROBE_INTERVAL_S=120`, `_POST_RECOVERY_GRACE_S=300`, `_OLLAMA_MAX_CONSECUTIVE_FAILURES=3`, `_COOLDOWN_AUTH_DEAD_S=3600` | All tuning lives at module top | `resurrect.py:311, 507, 518`, `offline.py:323`, `models.py:58` |
| Env hints | Env vars | `WINDY_SKIP_OLLAMA_WARMUP`, `WINDY_OLLAMA_TIMEOUT_S`, `WINDY_OLLAMA_MODEL`, `WINDYFLY_OFFLINE_QUEUE`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Test isolation + operator overrides + probe targets | `resurrect.py:253`, `offline.py:201`, `resurrect.py:580-592` |
| Sentinel "torn flag" branch | Return shape | `{"active": True, "model": None, ...}` on JSON decode failure | Refuses to drop lifeboat on corrupt write | `resurrect.py:110-116` |
| `_pick_offline_model` 4-tier fallback | Cascade | resurrect's chosen → env override → auto-pick → "llama3.2" literal | Silently shifts model under the lifeboat user | `offline.py:144-173` |
| "Local model error" text-marker dispatch | Magic string | `if "Local model error" in offline_response` | Decides whether to queue — couples loop to offline.py reply wording | `loop.py:938` |

---

## 4. Detected Smell — PR Pattern

| PR | Adds | What wedge it patched | "Fix the fix"? |
|---|---|---|---|
| **#138** | `is_resurrected`, `resurrect()`, `normalize()`, `/resurrect` slash | Original lifeboat — manual only | — (baseline) |
| **#145** | `auto_resurrect_attempt`, 60s cooldown, disable flag, `is_permanent_auth_error` (early version per comment at `resurrect.py:411`) | Chain-fail catch needed automatic entry without operator action | — (feature) |
| **#160** | (a) `/reset` calls `normalize` (b) Ollama timeout gets recovery_hint (c) `attempt_paid_recovery` + 120s probe (d) 🛟 prefix on lifeboat replies | "Bot stuck in lifeboat for 2h" — once IN, nothing got OUT | **YES — fixes PR #138's "no expiry" rule turning into a wedge.** Note tension: PR #138 docstring explicitly says "No expiry" (line 22-24); PR #160 walked that back. Discrepancy worth surfacing. |
| **#161** | `_paid_health_probe` (real key validation), `_POST_RECOVERY_GRACE_S` (5 min), `/lifeboat` status, `lifeboat.exited`/`recovery_failed` events | #160's `is_online()` was a reachability check — 401 looked "reachable" so the probe ping-ponged | **YES — fixes #160's probe.** Plus second-order: grace window exists *only* because #160's recovery + chain-fail catch could fire on the same turn. |
| **#166** | `queue_message` call in loop §1.7 lifeboat branch | "Your message is queued" copy from #160 was a lie — message never queued | **YES — fixes #160's user-facing promise vs. behavior gap.** (Not in your 6-PR list but tightly coupled — `test_lifeboat_actually_queues.py`.) |
| **#201** | Ollama timeout 30s→180s; warmup; context truncation; consecutive-failure counter; wedged-escape | #160's recovery only helped when paid came back. If paid stayed down AND Ollama timed out, user saw error on every chat | **YES — fixes the inverse of #160.** #160 = "paid back → exit"; #201 = "local broken → exit". Mirror-image edge case. |
| **#209** | `is_permanent_auth_error` (production version with both-signal requirement) + skip lifeboat on perma-auth | PR #145's `auto_resurrect_attempt` triggered on 401-invalid-x-api-key; every retry re-401'd; #201's escape brought it back to paid which 401'd again → wedge loop | **YES, doubly — fixes #145 AND fixes #201's mitigation.** Without #209, #201's escape + chain-fail-catch became a tighter ping-pong than the one #161 fixed. |
| **#210** | `_COOLDOWN_AUTH_DEAD_S = 3600`; perma-auth bypasses cooldown escalator in `models.py` | #209 only short-circuited at the *resurrect* layer. Chain still hammered Anthropic every 30-90s | **YES — fixes #46's exponential escalator under conditions #209 surfaced.** Same classifier hoisted into a parallel layer. |

**Onion pattern:** 5 of 7 PRs are direct or indirect fixes to earlier PRs in the chain. #138 and #145 are the only true features; #160/#161/#166/#201/#209/#210 form a "fix-the-fix" cascade. #209 alone fixes two prior PRs.

**Notable discrepancy:** PR #138 docstring (`resurrect.py:22-24`) says "No expiry. Pause has expiry-rationale (cost). Resurrection doesn't." — but every PR after #160 silently violates this. The docstring is stale.

**Notable bug:** `lifeboat_status()` at `resurrect.py:757` reads `state.get("at")` but `resurrect()` writes the key as `"ts"` (line 212). The `"since"` field in status output is always `None`. No test catches this — `test_status_when_resurrected` doesn't assert on `since`.

---

## 5. Refactor Target — Proposed Minimal FSM

Target: 5 states max. Treat orthogonal flags (auto-disabled, perma-auth-dead-classifier) as *guards* on transitions, not states.

```
                      ┌─────────────────────────────────────────┐
                      │                                         │
                      ▼                                         │
   ┌──────────┐   T1/T2   ┌──────────┐  T8 (probe ok)   ┌──────┴──────┐
   │ HEALTHY  ├──────────►│ LIFEBOAT ├─────────────────►│  RECOVERING │
   └────┬─────┘           └─────┬────┘                  │   (grace)   │
        │                       │                       └──────┬──────┘
        │ T4 (any fail)         │ T10/T11               T14    │
        │                       │ (wedged-escape)              │ (5-min elapsed
        ▼                       ▼                              │  OR user msg)
   ┌──────────┐            ┌──────────┐                         ▼
   │ DEGRADED │            │ AUTH_DEAD│◄────────────  back to HEALTHY
   └────┬─────┘            └──────────┘    (perma-auth
        │ T6                                classified)
        │ (success)
        └────► HEALTHY
```

**Proposed 5 states:** `HEALTHY`, `DEGRADED`, `LIFEBOAT`, `RECOVERING`, `AUTH_DEAD`.

**Mapping current → new:**

| Current state/transition | Maps to new | Notes |
|---|---|---|
| LIFEBOAT_HEALTHY + LIFEBOAT_PROBING + LIFEBOAT_WEDGED | `LIFEBOAT` | Collapse — internal probing/wedge are *behaviors during one tick*, not durable states |
| POST_RECOVERY_GRACE | `RECOVERING` | Promote to first-class; grace becomes its definition |
| PERMA_AUTH_DEAD | `AUTH_DEAD` | Currently exists only implicitly via 1h cooldown — make it explicit |
| AUTO_DISABLED, AUTO_COOLDOWN | guards on `HEALTHY→LIFEBOAT` | Not states |
| Provider cooldown dict | guard on `LIFEBOAT→HEALTHY` and `DEGRADED→LIFEBOAT` | Not a state per se |
| T1, T2 | `HEALTHY → LIFEBOAT` | manual + auto unified |
| T7, T9 | `LIFEBOAT → LIFEBOAT` (self-loop with probe side-effect) | Probe is action, not transition |
| T8 | `LIFEBOAT → RECOVERING` | |
| T10 + T11 | `LIFEBOAT → HEALTHY` (wedged-escape) | |
| T3 | `HEALTHY → AUTH_DEAD` | currently a "reply and stay HEALTHY" — promote to durable |
| T5 | `DEGRADED → AUTH_DEAD` | when perma-auth classifier hits during a regular failure |
| T12, T13 | `* → HEALTHY` (user override) | Always-allowed escape hatch |
| T14 | `RECOVERING → HEALTHY` | Time-based |
| T15 | self-loop on `RECOVERING` blocking T1/T2 | Already correct semantics; just clearer now |
| T16 | self-loop on `HEALTHY` (auto-cooldown reject) | |
| T17 | side-effect of `LIFEBOAT` reply path | Should NOT be FSM state |
| T18, T19 | configuration | Not FSM |

**Transitions that DON'T fit cleanly — design questions to revisit:**

1. **`AUTH_DEAD` is currently emergent, not durable.** Today the bot replies "API key looks invalid" and *stays HEALTHY* with the provider in 1h cooldown. Should it actually *be* in `AUTH_DEAD` (refusing to attempt paid calls) until the operator triggers a credential-reload event? Currently the next call still walks the chain and hits cooldown skip.
2. **Where does the per-provider `_provider_cooldowns` dict live?** It's process-local module state — survives no restart, not shared across multi-process deployments. Refactor should either (a) own this in the FSM or (b) explicitly call it out as "circuit-breaker policy, separate concern."
3. **The "torn flag" branch** (`resurrect.py:110-116`) returns `{"active": True, "model": None}` — what state is that? Currently it's LIFEBOAT_HEALTHY-ish but `_pick_offline_model` will silently fall back to "llama3.2" literal which may not be installed → instant LIFEBOAT_WEDGED on next turn.
4. **POST_RECOVERY_GRACE is a leak.** The marker file is never cleaned up after expiry — `_within_post_recovery_grace` does mtime math but old markers persist forever. Refactor should decide whether `RECOVERING` is a transient state cleared on first turn after expiry.
5. **Two perma-auth code paths** (`resurrect.py:399` classifier called from both `resurrect.py:471` and `models.py:87`). Lazy import + duplicate intent. Should the FSM own the classifier?
6. **`lifeboat_status` "since" bug** (line 757 reads `"at"`, writes `"ts"`) — pin behavior in a test before refactor or fix in flight.

---

## 6. Test Coverage Map

| Transition | Test(s) | Gap? |
|---|---|---|
| T1 (manual /resurrect) | `test_resurrect.py`, `test_lifeboat_hardening.py::TestLifeboatStatus::test_status_when_resurrected` | covered |
| T2 (auto chain-fail) | `test_auto_resurrect.py::test_chain_fail_with_auto_on_and_ollama_available_prepends_notification` | covered |
| T3 (perma-auth dedicated reply) | `test_no_resurrect_on_401.py::test_agent_respond_surfaces_dedicated_auth_reply_on_401` | covered |
| T4 (provider cooldown set) | `test_cooldown_perma_auth.py::test_transient_failure_uses_short_cooldown` | covered |
| T5 (perma-auth long cooldown) | `test_cooldown_perma_auth.py::test_permanent_auth_failure_uses_long_cooldown`, `test_403_permission_uses_long_cooldown` | covered |
| T6 (cooldown clears on success) | `test_cooldown_perma_auth.py::test_success_clears_cooldown` | covered |
| T7 (probe scheduling) | `test_lifeboat_hardening.py::TestPaidHealthProbe` series | covered |
| T8 (recovery success → grace) | `test_lifeboat_hardening.py::TestPostRecoveryGrace::test_recovery_success_marks_grace`, `test_lifeboat_stuck_state_recovery.py::test_paid_healthy_clears_flag_and_returns_notice` | covered |
| T9 (probe fails, stay) | `test_lifeboat_stuck_state_recovery.py::test_paid_unreachable_keeps_flag` | covered |
| T10 (counter ticks to wedge) | `test_lifeboat_timeout_hardening.py::test_should_escape_only_after_three` | covered |
| T11 (wedged-escape) | `test_lifeboat_timeout_hardening.py::test_wedged_lifeboat_escapes_after_three_failures` | covered |
| T12 (/normal) | `test_lifeboat_stuck_state_recovery.py::test_normalize_called_when_panic_handler_fires` (indirect — uses normalize), `test_resurrect.py` (assumed) | covered |
| T13 (/reset panic) | `test_lifeboat_stuck_state_recovery.py::TestPanicClearsResurrectFlag::test_panic_handler_source_invokes_normalize` (static source-scan only — does NOT run the panic handler end-to-end) | **PARTIAL — static text check, not behavioral** |
| T14 (grace expiry) | `test_lifeboat_hardening.py::test_auto_resurrect_works_after_grace_expires` | covered |
| T15 (grace blocks re-entry) | `test_lifeboat_hardening.py::test_auto_resurrect_skipped_during_grace` + `TestAntiPingPongIntegration::test_recovery_then_paid_fail_does_not_re_resurrect` | covered |
| T16 (auto-cooldown reject) | `test_auto_resurrect.py::test_cooldown_blocks_rapid_attempts` | covered |
| T17 (lifeboat-timeout queues) | `test_lifeboat_actually_queues.py::test_lifeboat_timeout_actually_queues` | covered |
| T18/T19 (toggle) | `test_auto_resurrect.py::test_set_disabled_writes_flag`, `test_set_enabled_clears_flag` | covered |

**Gaps & risk areas for refactor:**

| Gap | Risk during refactor |
|---|---|
| No end-to-end test for T13 (panic /reset wired through full telegram handler) | Refactor that moves `normalize()` call could pass static `_invokes_normalize` test but break behavior |
| **`lifeboat_status()` "since" bug** — no test asserts `since` value | Refactor that fixes `"at"`→`"ts"` (correct) could be marked as breaking by no test, or persistent bug could survive |
| Torn-flag branch (`resurrect.py:110-116`) — no test forces a corrupt JSON | Refactor that tightens parsing could regress the "stay in lifeboat with unknown metadata" intent |
| No test for `_pick_offline_model` 4-tier cascade (`offline.py:144-173`) | Easy to silently drop a tier |
| `auto_resurrect.skipped` event reason values not enumerated in test | Refactor that renames reasons could break dashboards without warning |
| `lifeboat.exited` / `lifeboat.recovery_failed` payload schema not asserted (only event names) | Schema drift possible |
| Concurrent multi-turn / multi-process behavior on flag files | Atomicity claimed (line 25) but no contention test — refactor to lockless FSM risks races |
| Per-provider cooldown is module-level — tests `setup_function` clears it (`test_cooldown_perma_auth.py:28-31`); no test for process-restart loss | Refactor to persistent cooldown store unguarded |
| Default-model literal `"llama3.2"` fallback (`offline.py:156, 173`) — no test for "Ollama installed but bare-name model missing" | Specific failure mode silently broken |

---

## Implementation entry points for Phase 2.2.2

- `src/windyfly/agent/resurrect.py`
- `src/windyfly/agent/offline.py`
- `src/windyfly/agent/loop.py` (§1.7 at lines 843-943 and chain-fail catch at lines 1084-1187 are the two integration points the FSM must own)
- `src/windyfly/agent/models.py` (cooldown layer — parallel perma-auth path)
- `src/windyfly/channels/telegram_bot.py` (panic /reset wiring at 1633-1663, /normal at 1549-1580, /lifeboat at 1582-1599)
