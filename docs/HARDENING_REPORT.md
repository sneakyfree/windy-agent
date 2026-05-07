# Wave 11 — Adversarial Hardening Report

Prepared as a hostile QA engineer. The goal of this pass was to prove
the code doesn't work and fix the real issues — not to produce a green
checkmark.

**Scope:** clean-room `windy go`, `windy selftest --full`, `windy keys
rotate`, `/hatch/remote` attack surface, SSE event-order drift, and the
interaction between editable-install and the quickstart config writer.

**What this report is NOT:** a live end-to-end verification against the
real eternitas.ai / windypro.com / chat.windyword.ai / mail.windymail.ai
/ cloud.windyword.ai services. Those backends are not reachable from
this machine, so every "verify against real backend" bullet below is
marked **PENDING (live)** and seeded with a reproduction recipe rather
than a result.

---

## Severity legend

- **BROKEN** — feature is claimed to work but demonstrably doesn't. Fix required before ship.
- **DEGRADED** — works, but in a way that actively misleads, loses data on edge cases, or fails silently.
- **MISSING** — documented / implied behaviour is not implemented at all.
- **WORKS** — tested, confirmed, no finding.

The "Fixed in this PR" column tracks which findings were repaired on `wave11/hardening`. Everything else is seeded as a follow-up issue.

---

## Findings

| # | Severity | Area | One-line | Fixed in this PR |
|---|----------|------|----------|------------------|
| 1 | BROKEN | quickstart | `windy go --key ...` calls interactive `Prompt.ask()` on stdin; EOF-crashes under CI / pipes. | ✅ |
| 2 | BROKEN | /hatch/remote | No rate-limit on the gateway endpoint that spawns Python subprocesses. | ✅ |
| 3 | BROKEN | /hatch/remote | No max-length on body fields (`owner_name`, `broker_token`, etc.); multi-MB argv abuse possible. | ✅ |
| 4 | BROKEN | selftest | `selftest --full` short-circuits on a red base test and never reaches the ecosystem phase. | ✅ |
| 5 | DEGRADED | SSE events | `eternitas.registered` / `birth_certificate.ready` fire on failure too — event name alone is a misleading green tick. | ✅ |
| 6 | DEGRADED | SSE events | Success semantics inconsistent — some events carried `ok: bool`, others didn't. | ✅ |
| 7 | DEGRADED | quickstart UX | `"windy go"` prints `Set URLs in windyfly.toml  section to connect…` — Rich markup swallowed `[ecosystem]` as a style tag. | ✅ |
| 8 | DEGRADED | quickstart | `_install_deps()` crashes with unhandled `FileNotFoundError` when `uv` / `bun` aren't on PATH (and prints a bogus "✓ deps" for the missing one). | ✅ |
| 9 | MISSING | /hatch/remote | `passport_number` in the request body is silently ignored — Eternitas always mints a fresh passport. Caller intent vanishes. | Documented only |
| 10 | DEGRADED | editable install | `get_project_root()` walks upward to the source tree; running `windy go` from `/tmp` **rewrites the dev tree's `.env` + `windyfly.toml`**. | Documented only |
| 11 | BROKEN | quickstart | `windy go` leaks `brain` + `gateway` daemon processes when the parent is killed; no cleanup path. | Documented only |
| 12 | BROKEN | /hatch/remote auth | Endpoint accepts any 8-char `broker_token` without validating against Pro — an attacker triggers the full subprocess + outbound fan-out for free. | Documented only |
| 13 | WORKS | selftest ecosystem accuracy | Endpoint URLs verified against Wave 9 `DEPLOY.md §3`; reachable services show PASS, unreachable show FAIL with accurate latency. | n/a |
| 14 | WORKS | SSE event ordering | All 13 canonical events emit in contract order on an in-process mock hatch (see `test_event_stream_order_matches_contract`). | n/a |
| 15 | PENDING (live) | keys rotate cascade | Need a live Pro + Mail + Cloud + Chat to confirm old `wk_` key actually gets 401 after rotate, and that `--hard` cascade takes effect. | Review-only |
| 16 | PENDING (live) | Eternitas/Pro/Matrix/Mail/Cloud reality | None of the real backends are reachable from this workstation; the "✓ verified" messages from the mock path match the mock records, nothing more. | Review-only |

---

## Detailed findings

### Bug 1 — `windy go --key ...` crashes mid-ceremony on closed stdin (BROKEN, fixed)

**Repro:**
```bash
python3.13 -m venv /tmp/cr && /tmp/cr/bin/pip install -e .
mkdir /tmp/cr-run && cd /tmp/cr-run
echo "" | /tmp/cr/bin/windy go --key sk-fake-test-123 --model gpt-4o-mini --byok
```

**Captured output (before fix):**
```
What's my name?
  Name your agent (Windy Fly):   Orchestrator error (EOF when reading a line), falling back...
  Provisioning Windy Chat bot...
  ○ Windy Chat — skipped (no Synapse secret available)
  ○ Windy Mail — skipped (no Eternitas passport)
```

**Why it's broken:** `_try_hatch_provisioning()` (`src/windyfly/quickstart.py:~600`) unconditionally calls `Prompt.ask("Name your agent")`, then `Confirm.ask(...)`, then three more prompts for owner name/phone/email — even from the non-interactive `_go_noninteractive()` code path. Rich's `Prompt.ask` raises `EOFError` on closed stdin, which the orchestrator swallows into a `"fall back"` message. The hatch then claims `✓ Brain started` / `✓ Gateway started` as if everything succeeded, but Eternitas + Mail are "Pending" in the ecosystem table — a silent, user-visible contradiction.

**Fix:** Added `non_interactive: bool = False` parameter to `_try_hatch_provisioning`. `_go_noninteractive` passes `True`. When set, every `Prompt.ask` is bypassed and values are sourced from env vars or defaults (`WINDYFLY_AGENT_NAME` → "Windy Fly", `WINDY_OWNER_NAME/OWNER_PHONE/OWNER_EMAIL` → empty). See `quickstart.py`.

**Verification:** Re-ran the clean-room repro after the fix — no "EOF" / "Orchestrator error" strings. All five mock services provisioned in 32s.

---

### Bug 2 — `/hatch/remote` has no rate limit (BROKEN, fixed)

**Where:** `gateway/src/server.ts` routes `/hatch/remote` to `handleHatchRemote(req)` without calling `isRateLimited`. Every POST spawns `uv run python -m windyfly.hatch_remote`, which fans out to Eternitas / Mail / Cloud / Matrix / Phone.

**Attack:** 100 POSTs to `/hatch/remote` in a tight loop from one IP → 100 Python subprocesses running concurrently + 100 × 5 = 500 outbound ecosystem calls. No backpressure.

**Fix:** Added `isRateLimited(clientIP, "upstream")` gate at the top of the `/hatch/remote` branch in `server.ts`. Reuses the existing `upstream` bucket (30 req/min/IP) — semantically a good fit since providers/validate uses the same bucket for similar CPU+outbound fan-out. Returns HTTP 429 with a `Retry-After: 60` header when tripped.

**Not fixed here:** the X-Forwarded-For header is attacker-controlled behind a misconfigured reverse proxy; bucket cardinality-attack is possible (spray 100k unique IPs via XFF). Documented as a follow-up — needs a trust list or `Forwarded:` header instead.

---

### Bug 3 — No length caps on `/hatch/remote` body fields (BROKEN, fixed)

**Where:** `gateway/src/hatch-remote.ts::validateHatchRemoteBody` only required each field to be a string. An attacker could send `owner_name = "X" * 10_000_000` and eat 10 MB of argv / Python-process env.

**Fix:** Added per-field `MAX_LEN` table:
```ts
windy_identity_id: 128, passport_number: 64, broker_token: 512,
owner_email: 254 (RFC 5321), owner_phone: 32, owner_name: 200, agent_name: 120
```
Validator rejects anything over. Tests added to `gateway/tests/hatch-remote.test.ts` for every field (5 new length-cap tests).

---

### Bug 4 — `selftest --full` never runs the ecosystem phase when the base test fails (BROKEN, fixed)

**Where:** `cli_selftest.py::run_full_self_test` called `run_self_test()` first; that function `sys.exit(1)`'d on any internal failure. The ecosystem phase was unreachable in exactly the scenario where operators most need it (agent can't reach LLM → they need to know if the ecosystem is also down).

**Repro (before fix):** `windy selftest --full` on a machine with `DEFAULT_MODEL` set but no API key → `✗ Response received: OPENAI_API_KEY not set` → hard exit, no ecosystem table rendered.

**Fix:** `run_self_test` now accepts `exit_on_failure: bool = True` and **returns a bool** instead of exiting directly. `run_full_self_test` calls it with `exit_on_failure=False`, runs `run_ecosystem_health` unconditionally, then reports both and exits non-zero iff either failed. Test added: `test_full_runs_ecosystem_even_when_base_selftest_fails`.

---

### Bug 5 — `eternitas.registered` / `birth_certificate.ready` fire even on failure (DEGRADED, fixed)

**Where:** `hatch_orchestrator.orchestrate_hatch` emitted these events unconditionally after each step. The event name promises success; the payload had to be inspected (`passport_id: ""`, `certificate_number: ""`) to realize otherwise. An Electron consumer that flipped a spinner→checkmark on event name alone would lie to the user.

**Fix:** Every terminal success event (`eternitas.registered`, `*.provisioned`, `phone.assigned`, `birth_certificate.ready`, `hatch.complete`) now carries `ok: bool`. Consumers must gate on `data.ok`, not the event name. Contract pinned with `test_every_phase_event_carries_ok_flag`.

### Bug 6 — inconsistent event success semantics (DEGRADED, fixed)

Rolled into Bug 5. Previously `mail.provisioned`, `chat.provisioned`, `phone.assigned`, `cloud.provisioned` had `ok` but `eternitas.registered`, `birth_certificate.ready`, `hatch.complete` didn't. All seven are uniform now.

---

### Bug 7 — Rich markup swallows `[ecosystem]` (DEGRADED, fixed)

**Before:** `Set URLs in windyfly.toml  section to connect to real services` (double-space where `[ecosystem]` should be).

**Cause:** `hatching.py:354` called `console.print("  [dim]…[ecosystem] section…[/dim]")`. Rich parses `[ecosystem]` as an open style tag with no matching close — it's treated as markup and emits nothing.

**Fix:** Escape the brackets with a backslash so the literal `[ecosystem]` prints: `r"  [dim]Set URLs in windyfly.toml \[ecosystem] section …[/dim]"`.

---

### Bug 8 — `_install_deps()` crashes when `uv`/`bun` missing (DEGRADED, fixed)

**Before:** `subprocess.run(["uv", "sync"], ...)` raised `FileNotFoundError` if `uv` wasn't on PATH. On a pure pip-install (no source tree) the code would then print `✓ Python deps` for a subprocess that threw — because the guard was wrong.

**Fix:** Wrapped both `uv sync` and `bun install` in `try/except FileNotFoundError`, plus a pre-check that the project actually has a `pyproject.toml` / `gateway/package.json` to sync. Pip-install path now prints `○ Python deps skipped (pip install — no source tree)` instead of fabricating a tick.

---

### Bug 9 — `passport_number` in `/hatch/remote` body is ignored (MISSING, documented)

**Where:** `src/windyfly/hatch_remote.py::run` sets `os.environ["ETERNITAS_PASSPORT"] = passport_number`, but `hatch_orchestrator._step_eternitas` builds a `RegistrationRequest` that **doesn't include `passport_number`** (see `RegistrationRequest` in `eternitas/models.py`). Server mints a fresh passport regardless.

**Consequence:** A caller that wants to resume a hatch for an existing passport has no supported path; the field is vestigial.

**Proposed fix (not in this PR):** Either thread `passport_number` through `RegistrationRequest` and Eternitas's `/bots/register` → upsert-by-passport, or remove the field from the request schema and update Wave 8 docs. Decision needs Grant + Eternitas owner.

---

### Bug 10 — editable-install resolves `PROJECT_ROOT` upward, trashes dev tree (DEGRADED, documented)

**Where:** `platform.py::get_project_root` step 3:
```python
source_root = Path(__file__).resolve().parent.parent.parent
if (source_root / "pyproject.toml").exists():
    return source_root
```

**Observed during Wave 11:** Ran `cd /tmp/wave11-hatch-run && windy go --key …`. The `.env` and `windyfly.toml` files **landed in `/Users/thewindstorm/windy-agent/`**, not `/tmp/wave11-hatch-run`. `git status` showed the dev tree's `windyfly.toml` modified.

**Consequence:** Any developer who runs an editable `windy go` from anywhere outside their source checkout will overwrite their in-progress config. For real pip-installed users this is a non-issue (no `pyproject.toml` upstream of the install site-packages), but it's a latent dev footgun.

**Proposed fix (not in this PR):** Move step 2 (CWD-with-marker) and step 3 (source checkout) decision: prefer CWD when a marker is present, otherwise fall back to `WINDYFLY_HOME`, then `~/.windyfly`, then source tree only as a last resort for `uv run ...` dev invocations.

---

### Bug 11 — `windy go` leaks daemon processes on SIGTERM (BROKEN, documented)

**Repro:**
```bash
/tmp/cr/bin/windy go --key sk-... --byok &
PID=$!
sleep 5 && kill $PID
# PID's children survived — the bun gateway is still listening on :3000.
pgrep -af "bun run src/server.ts"   # →  PID of orphan gateway
```

**Cause:** `cmd_start` (in `quickstart.py::_launch` → `cli.py::cmd_start`) spawns brain + gateway via `subprocess.Popen(..., start_new_session=True)` (daemon mode default in --byok flow via `args.daemon` path) and then returns. No `atexit` hook or signal trap on the parent — TERM'ing the parent leaves the children alive.

**Why it's bad for Wave 11:** the Wave 9 smoke-test + any CI that times out waiting for `windy go` leaves zombie gateways bound to :3000. The next test run then fails on port collision.

**Proposed fix:** Register a SIGTERM handler in the quickstart that forwards to the PID file and calls the same cleanup code `windy stop` uses.

---

### Bug 12 — `/hatch/remote` accepts any 8-char broker_token without validating against Pro (BROKEN, documented)

The gateway's only check is `broker_token.length >= 8`. A malicious caller sends `"12345678"` and the Python subprocess happily sets it as the LLM API key in the process env. Downstream LLM calls will 401, but the Python process has already fanned out to Eternitas / Mail / Phone / Cloud for free.

**Proposed fix:** gateway should call Pro's `/api/v1/broker/validate` (or equivalent) before spawning the subprocess, or Pro must sign the token with a short-lived HMAC that the gateway verifies locally. Coordinate with Wave 8 fix-2 (HMAC broker contract) — the `X-Windy-Signature` outbound path already exists for the Pro-→Agent call; the reverse needs a symmetric receiver endpoint on Pro or a JWT-shaped token we can verify with the cached JWKS.

---

## Fixes landed on `wave11/hardening`

| File | Change |
|---|---|
| `src/windyfly/quickstart.py` | `_try_hatch_provisioning(non_interactive=...)`; non-interactive path skips every `Prompt.ask`; `_install_deps` tolerates missing `uv`/`bun` + no source tree. |
| `src/windyfly/cli_selftest.py` | `run_self_test(exit_on_failure=...)` returns bool; `run_full_self_test` always runs ecosystem phase. |
| `src/windyfly/hatch_orchestrator.py` | Every `.registered`/`.provisioned`/`.ready`/`hatch.complete` event carries `ok: bool`. |
| `src/windyfly/hatching.py` | Escaped `[ecosystem]` Rich-markup tag so the hint prints correctly. |
| `gateway/src/hatch-remote.ts` | `MAX_LEN` table + per-field length caps. |
| `gateway/src/server.ts` | `isRateLimited(ip, "upstream")` gate before `handleHatchRemote`. |
| `tests/test_hatch_remote_events.py` | New `test_every_phase_event_carries_ok_flag`. |
| `tests/test_selftest_ecosystem.py` | New `test_full_runs_ecosystem_even_when_base_selftest_fails`. |
| `gateway/tests/hatch-remote.test.ts` | 5 new length-cap tests. |

**Test health post-fix:** 1290/1290 Python (+1 new), 59/59 gateway (+5 new), `mypy` clean on py 3.12/3.13/3.14, `ruff check src/` clean.

---

## Live-verification backlog (can't exercise from this workstation)

Seeds for follow-up tickets once a staging ecosystem is reachable:

1. **Eternitas passport reality** — after a real hatch, `curl https://api.eternitas.ai/api/v1/registry/verify/<passport>` must return 200 with a record matching the birth certificate. If 404, `_step_eternitas` is falsely reporting success.
2. **Mail inbox reality** — `curl https://mail.windymail.ai/api/v1/accounts/<passport>` must show the provisioned account. If missing, mail's `_step_mail` success claim is a lie.
3. **Matrix identity reality** — `curl <homeserver>/_synapse/admin/v2/users/@<bot>:<homeserver>` (admin-auth required) must return the bot row.
4. **Cloud quota reality** — `curl https://cloud.windyword.ai/api/v1/identity/by-passport/<passport>` must show a UserPlan.
5. **Phone number reality** — Twilio console lookup on the assigned number; verify the release date + webhook URL.
6. **`windy keys rotate` cascade reality** — run rotate, then send a Mail message with the OLD `wk_` token → must be 401. Run a `/api/v1/archive/...` with the OLD token → 401.
7. **Rotate-during-in-flight-send race** — start a large-attachment Mail send in one terminal, run `windy keys rotate` in another; does the in-flight send complete cleanly or 401 partway?
8. **Ctrl-C during rotation (live)** — simulate network partition at each step of rotate; confirm `windy keys show` post-kill reflects reality (either old or new, never half).
9. **`/hatch/remote` flood (live)** — 100 POSTs/sec from one IP → confirm HTTP 429 from the new rate limiter kicks in.
10. **`/hatch/remote` malformed body matrix** — fuzz every field with nulls, integers, deeply nested objects, unicode edge cases.
11. **Passport collision (live)** — fire two concurrent hatches with the same `windy_identity_id` → do we get two passports (bug) or one (idempotent)?
12. **SSE ordering under real latency** — capture a real Electron SSE stream during a cold-start hatch; run it through the Wave 9 `scripts/smoke-test.sh` ordering parser.

Each bullet above is the start of a reproduction recipe; attach it to a ticket when staging comes up.

---

## Non-finding: things that held up under adversarial poking

- **Base SSE event ordering.** `orchestrate_hatch` emits the 13 canonical events in the contract order pinned by `test_event_stream_order_matches_contract`. Adding per-event `ok` flags didn't change ordering.
- **Ecosystem health output accuracy.** Pointed at `https://*.invalid.example` hostnames; the table correctly showed FAIL for Eternitas/Pro (critical) and WARN for Matrix/Mail/Cloud (optional). Latency figures were real (100–900ms for DNS NXDOMAIN round-trips).
- **Dashboard auth fail-closed.** `WINDYFLY_ENV=production` + empty `DASHBOARD_PASSWORD` still refuses to boot. Wave 7's P1-S5 guard remains intact.
- **HMAC broker signing.** `sign_broker_request` still canonicalizes the body with `sort_keys=True` + compact separators; the X-Windy-Timestamp header still records `int(time.time())` *before* the HMAC (Wave 8 fix-2/6).
- **Trust webhook verifier (`verify_hmac` / `verify_jws`).** Wave 7 + the "accept `sha256=<hex>`" constant-time compare shape are unchanged.
