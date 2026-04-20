# Windy Fly Gateway — White-Glove Smoke Report

**Target:** `https://fly.windyword.ai` (EC2 `i-09ed54184d0f536ab`, EIP `174.129.41.59`, t3.small)
**Date:** 2026-04-19 (tests executed 2026-04-20 00:30–00:45 UTC)
**Scope:** Wave 13 Phase 5 production deploy — discovery only, no fixes applied.
**Brief:** `docs/WHITE_GLOVE_SMOKE_PROMPT.md`
**Default branch:** `master`

## Summary

| Severity | Count |
|----------|-------|
| **P0**   | 2     |
| **P1**   | 2     |
| **P2**   | 6     |
| **P3**   | 5     |

**Ship verdict: NOT READY.** Two P0s give any internet caller full unauthenticated read/write access to every proxied endpoint and all three WebSocket surfaces (including the PTY terminal relay). Both are caused by the same assumption the code makes: *peer-IP from `server.requestIP()` is trustworthy.* Behind nginx, it never is.

Zero forged broker-token acceptance — the Wave 12 cryptographic gate holds end-to-end. Pro's `/credentials/verify` endpoint is live and the contract is tight.

---

## §1 — Public surface

### P2 — `/api/webhooks/trust` 500s on every request — observed: HTTP 500 `{"error":"Not connected to Python brain"}` — expected: 401 on bad HMAC, 400 on bad shape, 2xx on valid Eternitas webhook

Repro: `curl -X POST -H "Content-Type: application/json" -d '{}' https://fly.windyword.ai/api/webhooks/trust` → 500.

The trust webhook receiver proxies to `bridge.call("trust.webhook", …)`, which needs a Python brain on the UDS socket `/tmp/windyfly.sock`. No Python brain runs on this EC2 box (runbook says agent runtime lives on user machines), so every inbound trust webhook 500s. An Eternitas → Fly webhook fan-out will throw in Eternitas. Note that the malformed-JSON path (`--data-binary 'not{json['`) correctly returns 400; the 500 only triggers once JSON parsing succeeds.

Fix: either stand up a Python brain on this host, or short-circuit `/api/webhooks/trust` to return 503 with a clear "receiver disabled on this deployment" message so upstream (Eternitas) doesn't treat it as a 5xx-retryable.

### P2 — Route surface is ~90% dead code on this deployment — observed: 26 proxy routes all return `_offline:true` — expected: these routes shouldn't be exposed at all on a stateless gateway

Repro: `for r in /api/sliders /api/cost/daily /api/intents /api/dashboard /api/memory/search /api/personality/history /api/skills /api/moments /api/failures /api/mode /api/offline/status /api/events /api/conflicts; do curl -sS https://fly.windyword.ai$r; done` — all return offline fallbacks.

The Phase 5 gateway's only business purpose is `/hatch/remote` + `/api/webhooks/trust` + `/` + `/api/health`. Every other route (sliders, cost, intents, dashboard, memory, personality, skills CRUD, decay, conflicts, moments, failures, mode, offline, events, providers/validate, machines/*) still parses bodies, applies CORS logic, and returns shaped JSON — attack surface without business purpose. If any parser has a latent CVE, it's exploitable here without serving a user.

Fix: needs-investigation — either ship a separate `server-gateway.ts` that only registers the 4 live routes, or guard the non-gateway routes behind `bridge.isConnected()` so they 503 before parsing. Discussion needed about whether this box will eventually host an agent runtime.

### P3 — SPA index.html leaks for any non-`/api/` 404 — observed: `GET /does-not-exist` → 200 with dashboard HTML — expected: 404

Repro: `curl https://fly.windyword.ai/wp-login.php` → full dashboard HTML, HTTP 200.

Intentional SPA client-routing behavior, but means a `/phpmyadmin`, `/.git/config`, `/wp-admin`-style probe gets the dashboard shell (674 bytes, nothing sensitive in the bundle). Mildly info-disclosive; noise in monitoring.

Fix: return 404 for any path that doesn't match a known SPA route or static asset.

### ✓ Clean handling verified

- JS bundle has no localhost/127.0.0.1/dev-token/Sentry-DSN/API-key strings (grep of `/assets/index-Cedg1p9o.js` returned zero matches for secret patterns).
- Malformed JSON → 400 "invalid JSON body".
- 10 MB body upload → 413 from nginx (client_max_body_size default 1M) — no way to overwhelm the subprocess with argv-size abuse beyond what `MAX_LEN` table guards.
- `/api/nonexistent-route-xyz` (starts with `/api/`) → clean 404 JSON.

---

## §2 — Dashboard auth

### P0 — Dashboard auth is completely bypassed behind nginx — observed: `GET /` → 200 + full dashboard HTML with NO cookie — expected: 401 login page

Repro: `curl https://fly.windyword.ai/` → HTTP 200, 674 bytes of dashboard HTML, zero auth challenge. Same for `GET /api/sliders`, `GET /api/dashboard`, `GET /api/cost/daily`, `PUT /api/sliders/humor` (503 from brain-offline, but the 503 is AFTER the auth check passed).

Root cause — `server.ts:297`:
```ts
if (isLocalhostRequest(req, server)) return null;  // skip auth
```
`isLocalhostRequest` reads `server.requestIP(req).address`, which is the *socket peer* address. For requests arriving via nginx → Bun on `127.0.0.1:3000`, the peer IS 127.0.0.1. Every public internet request appears to the Bun server as a local connection and the auth check is skipped outright. The X-Forwarded-For header nginx sets is correctly populated but never consulted for the auth decision.

The comment at `server.ts:160–167` explicitly says "read the peer's socket address, not the Host header" — correct advice for a direct listener, but fatally wrong for an nginx→Bun topology. Confirmed: DASHBOARD_PASSWORD is set and nonempty on the box; `validateDashboardAuthConfig` passed at boot. The bypass happens entirely via the loopback check.

Impact: every proxied endpoint (sliders, cost, intents, dashboard summary, memory search, personality history/drift, skills list, moments, failures, mode, offline status, events, conflicts) is world-readable. Every mutating POST (skills create/evaluate/promote/rollback/golden-tests/regression, decay/run, conflicts/:id/resolve, personality/snapshot, personality/rollback, sliders PUT, mode PUT) is world-writable — currently 503ing because the brain is offline, but the auth gate itself is open. The moment a Python brain comes online on this host, every agent-state mutation is unauthenticated.

Fix: needs-investigation. Two acceptable patterns:
1. Trust `X-Forwarded-For` from 127.0.0.1 only, reject the loopback check otherwise. Still requires trusting nginx.
2. Don't trust peer-IP for auth at all when `WINDYFLY_ENV=production`. Production always requires the password/cookie.

### P1 — Dashboard cookie value IS the raw DASHBOARD_PASSWORD — observed: `Set-Cookie: windy_auth=<entire-password-verbatim>` — expected: opaque session token

Repro (with correct password from `~/.windyfly-phase5-state`):
```
curl -X POST -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "password=$PW" https://fly.windyword.ai/api/auth/login
→ Set-Cookie: windy_auth=AyHarf9ifHMn3YWSf77kDReXvIrIZewg; …
```
The cookie value is `DASHBOARD_PASSWORD` verbatim (`server.ts:384`). Cookie attributes are good: HttpOnly, Secure, SameSite=Strict, Max-Age=86400.

Impact: any XSS, malicious browser extension, or social-engineered cookie export yields the master secret; the only way to revoke is to rotate `DASHBOARD_PASSWORD` in `/etc/windyfly/production.env` and restart the gateway. No per-session revocation.

Fix: mint a random session token per login, store `{token → expiresAt}` in memory (LRU), send the token as the cookie value. Logout / rotation invalidates single sessions without touching the master secret.

### ✓ Auth-bucket lockout works

Five wrong-password POSTs return 401; the sixth returns 429 with `Retry-After: 60`. Bucket isolation also confirmed — saturating `upstream` (30/min) does not reduce the `auth` bucket's 5/min budget.

---

## §3 — Broker-token verification contract with Pro

### P2 — No broker-verify cache (despite brief's claim) — observed: back-to-back identical forged-token requests take ~410ms each — expected per brief: second call near-zero latency

Repro:
```
T1=now; POST /hatch/remote {bk_live_CacheTest...}
T2=now; POST /hatch/remote {bk_live_CacheTest... same body}
T3=now
→ first-call-ms=417   second-call-ms=411
```
`grep -i "cache\|Map\|ttl\|memo" gateway/src/broker-verify.ts` returns zero matches. The brief's "Verify that broker-verify.ts's 5-min cache works" is documentation drift — the cache was never implemented.

Impact: every `/hatch/remote` hits Pro's `/credentials/verify`. At 30/min (upstream bucket cap), that's 30 QPS against Pro's `ratelimit-policy: 120;w=60` (i.e. 2 QPS on Pro). Pro is currently the throttle bottleneck on the hatch ceremony. Today's traffic is zero so it doesn't matter, but a viral Grandma-Ribbon moment (single Pro tenant launching 60 hatches in a minute) will start hitting Pro's 120/min ceiling. No backpressure in the gateway; Pro 429s translate to `pro_status_429` → 401 on hatch.

Fix: needs-investigation. A 30–60s in-memory cache keyed by `(broker_token, windy_identity_id)` → `BrokerTokenClaims` would fold the QPS down without loosening the identity/passport mismatch checks. Invalidate on any `ok:false` result.

### P3 — Stale `[broker-verify] Pro returned 404` log line — observed: one log line fires at 18:50:21 UTC (service start); Pro's endpoint has since deployed — expected: log line cleared or annotated

Repro: `sudo grep -c "broker-verify" /var/log/windyfly/gateway.log` → 1 hit: `Pro returned 404 on /api/v1/agent/credentials/verify — the endpoint is not deployed`. My smoke tests (3c, 3f) elicit `{ok:false,reason:"not_found"}` from Pro → gateway returns `token_not_found` → implies Pro's `/verify` is responding 200 correctly. Direct test of `https://api.windyword.ai/api/v1/agent/credentials/verify` with valid HMAC returns 200 + `{ok:false,reason:"not_found"}`.

Impact: an oncall engineer grepping for `broker-verify` sees a scary "endpoint not deployed" line that was true at boot but stale now. Low-noise issue.

Fix: downgrade the warn to single-fire-per-process with a counter, or clear by restarting the gateway. (Service restart would replay the ENV + reconnect to bridge.)

### ✓ Adversarial paths all reject correctly

| Case | Token | Status | Reason |
|------|-------|--------|--------|
| missing | (absent) | 400 | missing or invalid field: broker_token |
| too short | `short` | 400 | broker_token is too short |
| oversize | 608-char `bk_live_AAAA…` | 400 | field 'broker_token' exceeds 512 chars |
| bad format | `garbage1234` (no `bk_` prefix) | 401 | bad_format (no Pro round-trip — fast reject) |
| forged `bk_live_` | `bk_live_ForgedAttackerToken0000` | 401 | token_not_found (Pro-verified) |
| cache-probe forged | `bk_live_CacheTest1234567890` (twice) | 401 | token_not_found both times |

Pro's `/credentials/verify` contract confirmed: HMAC canonical string `<ts>.POST./api/v1/agent/credentials/verify.<sha256(body)>`, body `{"broker_token":"bk_live_..."}`, Pro returns 200 + `{ok:false,reason:"not_found"}` for unknown tokens and the gateway translates that to 401 for the caller. No forged token accepted in any tested shape. Attacker cannot distinguish "Pro is down" from "token is junk" by response body — both are 401.

### ⚠ End-to-end happy path not verified — Pro-side issue blocks mint

Attempting to mint a token against `POST https://api.windyword.ai/api/v1/agent/credentials/issue` with a well-formed signed body returns HTTP 500 `{"error":"broker_issue_failed"}` for a synthetic `windy_identity_id` like `wi_smoke_test`. Signature validates (using `identity_id` instead of `windy_identity_id` yields 400 with a clear field-name error, so signing is correct). Pro appears to require a pre-existing Windy identity row that this smoke had no way to create.

Full hatch-ceremony smoke (valid token → 13-event SSE → `hatch.complete.ok=true`) therefore remains untested end-to-end. Recommend a separate smoke that uses a real Grant-owned identity + hatch flow once a `wi_` exists.

---

## §4 — Hatch-remote SSE ceremony

### P1 — Subprocess survives client disconnect with no concurrent-process cap — observed: `cancel()` deliberately does not kill the Python subprocess — expected: bounded resource usage

`hatch-remote.ts:230–235`:
```ts
cancel() {
  // Client disconnected — nothing to do here; the subprocess
  // will see EOF on stdout when its pipe is collected. We
  // intentionally don't kill() the process: the Electron UI may
  // reconnect and the provisioning work is already in flight.
},
```
The design rationale is correct (Electron reconnect for Grandma-Ribbon) but the combination of:
- 30/min upstream bucket cap per IP
- 5-minute nginx `proxy_read_timeout`
- No global cap on concurrent hatch subprocesses
- No cap on elapsed subprocess runtime
- ~60-100 MB per Python subprocess
- t3.small has 1.9 GB RAM

gives an attacker with one valid `bk_live_` token a trivial OOM vector: 30 hatches in the first 60 seconds × 5-minute lifetime = up to 150 concurrent Python processes. 150 × ~80 MB ≈ 12 GB needed, machine has 1.9 GB → OOM before minute 1. nginx will 502 every other request during the OOM window; systemd will restart the gateway, the cycle repeats.

Fix: needs-investigation. Two layers recommended:
1. Bounded concurrency semaphore (e.g. max 8 concurrent hatches globally); reject 15th with 503.
2. Still kill subprocess on `cancel()` — 5-minute survival for Electron reconnect is a luxury that trades availability for a flaky-network edge case.

### P2 — SSE response headers can't be validated against disclosed 200 — observed: every test path returns 401 application/json — expected: one test should yield text/event-stream

All six `/hatch/remote` test payloads returned 401 (token rejected by Pro). The `text/event-stream` + `X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform` headers are set in `startHatchRemoteSse` (`hatch-remote.ts:238–250`) but only fire after broker-verify passes. Nginx `proxy_buffering off` + `proxy_read_timeout 300s` confirmed in `/etc/nginx/sites-enabled/windyfly` for `location = /hatch/remote`. Code review: correct.

Unverified: live SSE frame flow, 13-event ordering, final `hatch.complete.ok=true`, mid-stream disconnect subprocess cleanup. All blocked on obtaining a real `bk_live_` token (see §3 ⚠).

### ✓ Shape + method + body cleanliness

- `GET /hatch/remote` → 405 with `Allow: POST`.
- Invalid JSON → 400.
- 10 MB body → 413 from nginx (upstream never sees it).
- Every adversarial token shape returns 401 with a clear `reason` field.

---

## §5 — UDS proxy endpoints

### P2 — Every UDS proxy route is architectural dead code on this deployment

See §1 "Route surface is ~90% dead code" for the main finding.

- **26/26 GET routes** return `200 + _offline:true` with a sensible default payload (empty arrays, zeros, or static slider metadata).
- **10/10 mutating POST routes** return `503 + {"error":"Brain offline","_offline":true}`.
- **Zero 500s observed** on any UDS route — consistent offline fallbacks throughout.
- **bridge.ts gave up after 20 retries** (log: `Max reconnect attempts reached — giving up`). Even if a Python brain is later started on this host, the gateway needs a `systemctl restart windyfly-gateway` to reconnect.

Architectural question (brief §5): "If the gateway hosts no agent runtime, what's the point of these proxy endpoints on the deployed instance?" Answer: **none.** The Phase 5 runbook confirms agent runtime lives on end-user machines (`pip install windyfly && windy go`). These routes exist because the gateway binary is shared with a local-dev build where the Python brain *is* available. They're dead in production.

### P2 — Bridge gives up permanently after 20 retries — observed: `Max reconnect attempts reached — giving up` in log — expected: retry-forever with exponential backoff

Repro: `sudo grep "Max reconnect" /var/log/windyfly/gateway.log` → 1 hit at gateway boot sequence.

Once the bridge hits its cap, it stops trying. If the topology ever shifts (e.g. someone stands up a Python brain on this box for a debug session), the gateway has to be restarted to connect. Minor but surprising.

Fix: re-enable reconnect after a cooldown (e.g. retry once per hour forever) or remove the attempt cap. Low priority because the bridge isn't expected to be connected on this deployment.

---

## §6 — Personality + skills CRUD

All 7 personality routes + 7 skills routes tested — consistent behavior with §5. GETs return `_offline:true` payloads; POSTs return `503 + Brain offline`. No 500s. No cross-tenant leak path was testable because all responses are offline fallbacks with no tenant context — cannot assert 403 behavior without a live brain.

### ⚠ Cross-tenant 403 verification blocked — observed: all skill routes 503 — expected: a GET for another tenant's skills should 403 before 503

Without a live brain, every skill is empty, so the auth-vs-tenant layering can't be exercised. Recommend a follow-up smoke once a brain exists; grep the Python `skills.*` handlers for explicit `current_tenant_id == resource.tenant_id` checks. Document in `docs/HARDENING_REPORT.md`.

---

## §7 — WebSocket chat (and friends)

### P0 — All three WebSocket endpoints accept unauthenticated upgrades — observed: `wss://fly.windyword.ai/ws/chat`, `/ws/terminal/ANY`, `/ws/machine/ANY` all return 101 Switching Protocols with no auth — expected: 401 unless an auth cookie or broker token is presented

Repro:
```python
async with websockets.connect("wss://fly.windyword.ai/ws/chat", ssl=ctx) as ws:
    → ✅ OPEN (no auth)
    ws.send({"type":"ping"}) → {"type":"pong"}
    ws.send({"type":"message","content":"…"}) → {"type":"error","error":"Brain not connected"}
```
Same result for `/ws/terminal/unknown-machine-id-xyz` and `/ws/machine/unknown-id-xyz` (both open; silent because machineId is unknown).

Root cause — `server.ts:1547–1570`:
```ts
fetch(req, server) {
  const pathname = new URL(req.url).pathname;
  if (pathname === "/ws/chat") {
    if (server.upgrade(req, { data: { type: "chat" } })) return;
    ...
  }
  // same for /ws/terminal/:id, /ws/machine/:id
  return handleRequest(req, server);   // ← auth check lives HERE, after the WS returns
}
```
WebSocket upgrades short-circuit `handleRequest` before `checkDashboardAuth` ever runs. No auth, no rate limit, no CORS origin check, nothing.

**The P0 is `/ws/terminal/:machineId`.** `server.ts:1584–1587` auto-sends `{"type":"pty:create"}` on upgrade, relayed to the identified machine. If a `machineId` is known, guessable, brute-forceable, or ever appears in any URL / log / screenshot, anyone can open a PTY against that machine. Combined with §5's dead-code observation (no brain here), today no real `machineId` would succeed, but the bug is latent the instant a brain comes online.

Impact: universal WS DoS available today (anyone can open 10,000 WebSocket connections — no per-IP cap); remote shell the moment a live machineId exists.

Fix: needs-investigation. Apply `checkDashboardAuth` OR a broker-token check BEFORE `server.upgrade`. The pattern should mirror `/hatch/remote` which is exempt-but-gated-by-broker-token. For WS the broker token would need to live in a query param or subprotocol header because browser `WebSocket` API can't set arbitrary headers.

### ✓ Rate-limit bucket isolation for HTTP chat

`/api/chat` (HTTP, not WS): 60 successful 200s then 5× 429 — `chat` bucket cap 60/min confirmed. Bucket is independent from `auth`, `upstream`, and `setup`.

---

## §8 — Bucketed rate limits

### ✓ All buckets work at stated caps

| Bucket | Cap | Endpoint tested | Observed |
|--------|----:|-----------------|----------|
| auth | 5/min | POST /api/auth/login (wrong pw) | 5× 401, then 429 `Retry-After: 60` |
| chat | 60/min | POST /api/chat | 60× 200, then 5× 429 |
| upstream | 30/min | POST /hatch/remote (junk token) | 30× 401, then 3× 429 with `{"error":"rate limited — too many hatch requests from this IP"}` |
| setup | 10/min | Not exercised — no brain-backed setup route reachable on this deploy | — |

Bucket isolation verified — saturating `upstream` did not deplete `auth`, `chat`, or `setup`.

### P3 — Rate limit uses first X-Forwarded-For — observed: `clientIp()` reads `X-Forwarded-For` and splits on comma — expected: fine behind trusted nginx, but vulnerable if nginx ever changes

`server.ts:264–268` (`clientIp`): trusts the first XFF token. Today nginx appends `$proxy_add_x_forwarded_for`, so the leftmost value is the real client. But a direct listener (bypassing nginx) or an X-Forwarded-For header injected by the client would let an attacker spoof the rate-limit key. Low risk in current topology.

Fix: document the trust assumption in `server.ts:264` or pin the rate-limit key to the peer IP from `server.requestIP()` (inverted from the §2 finding — here peer-IP is the right trust anchor).

---

## §9 — CORS, headers, TLS

### P3 — No security headers on Fly gateway — observed: response headers contain only `Server, Date, Content-Type, Content-Length, Connection, Access-Control-*, Vary` — expected: HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP

Repro: `curl -I https://fly.windyword.ai/` — no `Strict-Transport-Security`, no `X-Content-Type-Options`, no `X-Frame-Options`, no `Referrer-Policy`, no `Content-Security-Policy`. For comparison, `https://api.windyword.ai/` ships all of:
```
strict-transport-security: max-age=15552000; includeSubDomains; preload
x-content-type-options: nosniff
referrer-policy: strict-origin-when-cross-origin
```
The Trust Dashboard is a React SPA on a public surface fronting a (future) code-execution agent. Missing CSP means if any XSS vector exists in the dashboard code, it's unmitigated.

Fix: add `add_header` directives to the nginx server block for HSTS (1y preload), X-Content-Type-Options: nosniff, X-Frame-Options: DENY, Referrer-Policy: strict-origin-when-cross-origin, and a tight CSP (`default-src 'self'; connect-src 'self' wss://fly.windyword.ai https://api.windyword.ai; frame-ancestors 'none'`).

### ✓ CORS allowlist correctly excludes unknown origins

- `OPTIONS` with `Origin: https://evil.example.com` → 204 with NO `Access-Control-Allow-Origin` header (browser will block).
- `OPTIONS` with `Origin: https://windyword.ai` → 204 with `Access-Control-Allow-Origin: https://windyword.ai`.
- `Vary: Origin` present → cache safety.

### ✓ TLS

- Cert: Let's Encrypt E8, `CN=fly.windyword.ai`, SAN `DNS:fly.windyword.ai`, valid `Apr 19 17:52 UTC — Jul 18 17:52 UTC`.
- HTTP → HTTPS: `GET http://fly.windyword.ai/` → 301 to `https://fly.windyword.ai/`.

---

## §10 — Cross-service contract with Pro

### ✓ Gateway calls Pro's /credentials/verify — confirmed via two independent sources

1. Adversarial smoke §3c: forged `bk_live_ForgedAttackerToken0000` → 401 `reason:token_not_found`. That `reason` string can only be produced via `token_${data?.reason}` in `broker-verify.ts:233` — implies Pro returned 200 JSON with `{ok:false,reason:"not_found"}`. The HMAC-signed verify round-trip is happening.
2. Direct curl to `https://api.windyword.ai/api/v1/agent/credentials/verify` with proper HMAC + body `{"broker_token":"bk_live_..."}` → HTTP 200, `{"ok":false,"reason":"not_found"}`. Pro's endpoint is live and contract-compliant.

### ⚠ Observed behavior when Pro returns non-200 (not exercised live)

`broker-verify.ts:210–222`:
- Pro returns 404 → `pro_verify_endpoint_missing` (happened at gateway boot before Pro PR #45 deployed; see §3 stale-log finding).
- Pro returns any other non-200 → `pro_status_<N>`.
- Pro times out (3 s default) → `pro_unreachable: <err>`.
- Pro returns malformed JSON → `pro_bad_json: <err>`.

All translate to 401 at the hatch boundary (fail-closed). No Pro-side data leaks because the gateway does NOT echo Pro's error body to the client — only the opaque `reason` label.

---

## §11 — Production observability

### P2 — SENTRY_DSN is empty — observed: `sudo grep SENTRY_DSN /etc/windyfly/production.env` → `SENTRY_DSN=` — expected: a real DSN for production error reporting

No `[gateway] Sentry initialized` log line ever fires. A code-execution agent shipping to production without external error reporting means the only record of a silent crash or panic is local `/var/log/windyfly/gateway.log`, which has no alerting attached.

Fix: populate `SENTRY_DSN` in `/etc/windyfly/production.env`, restart the gateway. Or pick any equivalent (Honeycomb, Datadog, etc.).

### P3 — broker-verify success path has no log line — observed: `sudo grep -c broker-verify /var/log/windyfly/gateway.log` → 1 (the stale 404) — expected: structured per-call log with outcome + latency

`broker-verify.ts` only logs on the `Pro returned 404` path and the `WINDY_BROKER_VERIFY_DISABLED` path. Every successful verify, every `token_not_found`, every `identity_mismatch`, every 5xx from Pro — silent. No way for ops to measure verify QPS, latency, or per-reason error rates in production.

Fix: add a one-line structured log (`[broker-verify] ok=true identity=wi_... latency=412ms` / `ok=false reason=token_not_found latency=410ms`) at the end of `verifyBrokerToken`.

### ✓ Service-level health

- `journalctl -u windyfly-gateway` since boot: zero WARN/ERROR from the gateway itself (only bridge reconnect noise, which is expected).
- Memory: 19.1 MB current, 25.9 MB peak across 5h 43min uptime. Well below t3.small's 1.9 GB.
- Process tree: single Bun process (pid 6114), no orphan `hatch_remote` subprocesses (I never got a valid hatch through, so this is expected null).
- Nginx access log breakdown across full day: 344× 200, 17× 429, 46× 401, 12× 503, 9× 400, 4× 404, 3× 500, 3× 204, 1× 302, 1× 405, 1× 413. The 3× 500 all trace to `/api/webhooks/trust` bridge failures (§1 finding). The 12× 503 are UDS proxy offline fallbacks. 46× 401 + 17× 429 + 9× 400 are my smoke tests.

---

## §12 — Cost discipline

### ✓ Instance + resources match runbook

- EC2: `i-09ed54184d0f536ab`, **t3.small**, running since 2026-04-19T18:42:07 UTC. Tags: `Environment=production, Project=Windy, Product=windyfly, Purpose=hatch-gateway, Name=windyfly-gateway`.
- EBS root: `vol-0868022668d89d0a5`, 20 GB, in-use. 2.8 GB / 19 GB used (filesystem level).
- EIP `174.129.41.59` (`eipalloc-06cd09015ac29948f`) associated with the instance — no unassociated EIP burn.
- Unattached EBS volumes in region: **0** (verified via `aws ec2 describe-volumes --filters Name=status,Values=available`).

### P3 — EBS volume `vol-0868022668d89d0a5` has no Project/Product tags — observed: `DescribeVolumes` shows tag values None/None for the fly-gateway volume — expected: Project=Windy, Product=windyfly inherited from instance

Other Windy EBS volumes (e.g. `vol-00acaff9d038197b1` for windy-chat, `vol-0b6ee1d7d31132175` for windy-pro) carry their instance tags. The fly-gateway volume does not. Cost attribution won't roll up to Windy/windyfly without manual grouping.

Fix: `aws ec2 create-tags --resources vol-0868022668d89d0a5 --tags Key=Project,Value=Windy Key=Product,Value=windyfly Key=Environment,Value=production`.

---

## What blocked full verification

1. **End-to-end hatch ceremony (13 SSE events):** blocked on the inability to mint a real `bk_live_` token. Pro's `/credentials/issue` returns HTTP 500 `broker_issue_failed` for synthetic `windy_identity_id` values. Needs a pre-existing Windy identity row or a Pro-side fix to the issue flow.
2. **Mid-stream SSE disconnect subprocess cleanup:** blocked on same — can't get past broker-verify without a real token.
3. **Cross-tenant skills 403:** blocked by the gateway having no Python brain; every skill response is offline.
4. **Setup bucket rate limit:** no setup/* route is reachable without a brain.

## Recommended next actions (NOT executed per discovery-only brief)

1. **Ship the P0 fixes first.** §2 (nginx loopback bypass) + §7 (WS unauth) should not reach another release.
2. **Ship the P1 fixes next.** §2 (cookie-is-password) + §4 (no hatch concurrency cap) before any production hatch traffic.
3. **Address the Pro-side `/credentials/issue` 500** so end-to-end smoke is possible.
4. **Populate SENTRY_DSN** and restart the gateway to clear the stale `Pro returned 404` log line while you're at it.
5. **Add security headers** at the nginx layer (HSTS, X-CTO, X-FO, Referrer-Policy, CSP).
6. **Tag `vol-0868022668d89d0a5`** for cost attribution.

All connections opened during this smoke (6 WebSocket probes, one 10 MB upload, rate-limit flood sequences, all curl requests) were closed with `--max-time` bounds; no leaked sockets observed server-side at audit end.
