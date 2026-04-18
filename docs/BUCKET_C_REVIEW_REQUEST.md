# Bucket C — Review requests

Nine PRs in the Wave 7 queue touch auth / crypto / identity / schema
/ webhooks. Per `docs/MERGE_TRIAGE.md` they **stop and wait for Grant
before merge**. For each, a one-paragraph summary, the specific
thing that needs a human reviewer skim, and the smoke test to run
right after merge.

Full playbook + rehearsal findings on
`https://github.com/sneakyfree/windy-agent/pull/1` (two comments).

---

## #1 — Waves 2-6 prerequisite

**Why high-risk:** 3,772-line base rollup. Contains Wave 2 (ecosystem
hatch) + Wave 3 (bot-key + trust gate) + Wave 4 (live Eternitas) +
Wave 5 (integration suite) + Wave 6 (prod prep). Touches every
high-risk surface: auth, crypto, identity, trust, schema migration,
webhooks. Every other Wave 7 PR targets this branch as its base —
merging it is the precondition for Buckets A and B landing cleanly.

**Needs eyes on:** the `trust_cache` schema migration #4 (it DROPs +
CREATEs the table, acceptable because the cache is ephemeral), the
new `auth/` and `trust/` packages' public APIs (make sure they match
what #3–#17 then import), the Eternitas `link_passport_with_identity`
shared-JWKS coupling note in `eternitas/provision.py`, and the
`_step_cloud_quota` / `_step_link_passport` ordering in
`hatch_orchestrator.py`.

**Smoke after merge:**
```bash
pytest tests/ --ignore=tests/visual_hatch_test.py --ignore=tests/integration -q
# Expected: 1114 passed, 37 skipped, 0 failing.
ETERNITAS_URL=http://localhost:8500 pytest tests/integration/ -v
# Expected when Eternitas is up: 27 passed.
```

---

## #3 — Fix P0-S3: gate slash commands against remote RCE

**Why high-risk:** Touches every channel adapter's command dispatch.
Before this PR, any configured channel (SMS/Matrix/Discord/…) could
receive `/run <shell>` and execute arbitrary code. The fix introduces
a channel-policy allowlist, threads the Eternitas trust gate into
`registry.execute`, marks `/run`, `/git`, `/web`, `/repl` as
`dangerous=True`, and drops `shell=True` from the actual subprocess
calls.

**Needs eyes on:** the `_REMOTE_ALLOWED_CATEGORIES` set in
`commands/registry.py` — any category you want remote users to access
must be there, and anything you don't must not. The trust-gate call
is fail-open on exception (so a downed Eternitas doesn't nuke the
CLI); confirm that's the intended production policy.

**Smoke after merge:**
```bash
pytest tests/contract/test_command_gate.py tests/test_command_registry.py -q
# Expected: all pass; 57 tests.
# Manual: from any remote channel (e.g. telegram), send "/run whoami".
#         Expected: "Command /run is not allowed from telegram."
```

---

## #5 — Fix P0-S1: check peer socket IP, not the Host header

**Why high-risk:** Changes the localhost gate that `/api/setup/*`
routes use. Before, a remote attacker sending `Host: localhost`
bypassed the gate. After, only the actual socket peer address
passes.

**Needs eyes on:** `isLoopbackAddress` — confirm the v4/v6 blocklist
is correct for your deploy environment (some container orchestrators
give containers non-127 loopback IPs; verify Bun reports
`::ffff:127.0.0.1` on Linux dual-stack listeners). Also verify
`server.requestIP(req)` returns non-null for localhost connections
in your runtime; the function returns false on null/undefined.

**Smoke after merge:**
```bash
bun test gateway/tests/localhost.test.ts
# Expected: 20 pass, 0 fail.
# Manual: `curl -H 'Host: localhost' http://<remote-ip>:3000/api/setup/status`
#         Expected: 401 Unauthorized (auth prompt) or 403.
```

---

## #6 — Fix P0-S2: kill ?auth= query-param password leak

**Why high-risk:** Replaces the GET-based login with POST
`/api/auth/login`. Cookie flags change to include `Secure`; login
now uses `crypto.timingSafeEqual`. **Conflicts with #8** — they both
rewrite `checkDashboardAuth` and the login flow. Resolve as a pair
per the playbook.

**Needs eyes on:** the new cookie attributes (`Secure` requires
HTTPS; on a local-dev deploy that's HTTP, the cookie won't be set —
confirm this is intended). The form submits JSON or
`application/x-www-form-urlencoded`; make sure your deployment's
reverse proxy (nginx/ALB) doesn't strip one.

**Smoke after merge:**
```bash
bun test gateway/tests/login.test.ts
# Expected: 7 pass, 0 fail.
# Manual: visit https://<deploy>/ without a cookie.
#         Expected: POST login form; `?auth=foo` in URL does nothing.
```

---

## #7 — Fix P0-T1+T2: wire trust.changed webhook + verify both signatures

**Why high-risk:** New gateway HTTP route (`POST /api/webhooks/trust`),
new Python verifier (`trust/verify.py`) that does HMAC-SHA256 +
detached ES256 JWS verification against Eternitas's JWKS. New
dependency: `cryptography>=42`. Closes the previously-nonexistent
webhook receiver.

**Needs eyes on:** the JWKS fetch caches at `data/eternitas_jwks.json`
with a 24h TTL — verify that path is writable in your deploy.
`ETERNITAS_WEBHOOK_SECRET` must be set in prod for HMAC to
verify; without it the verifier returns "HMAC secret not
configured" and denies. Confirm the fail-open-in-dev /
fail-closed-in-strict policy (`WINDYFLY_TRUST_STRICT=1`) matches
your prod config.

**Smoke after merge:**
```bash
pytest tests/contract/test_trust_webhook_verify.py -q
# Expected: 17 pass, 0 fail.
# Manual (needs Eternitas up on 8500 with the webhook signer enabled):
#   Trigger a trust.changed event; tail the brain's logs for
#   "Passport X cache invalidated".
```

---

## #8 — Fix P1-S5+S6+O5-auth: fail-closed, constant-time, rate-limited auth

**Why high-risk:** Changes dashboard-auth startup behaviour — empty
`DASHBOARD_PASSWORD` in `WINDYFLY_ENV=production` now throws at
import time (the process never reaches `Bun.serve()`). Adds
constant-time compare and a 5/min auth-failure rate-limit bucket.
**Supersedes #6** on the auth path.

**Needs eyes on:** the production signal `WINDYFLY_ENV=production`
— confirm your deployment pipeline sets it. A forgotten env var here
now takes your dashboard offline rather than silently opens it;
that's the intended trade but flag it to whoever runs the deploy.
Also: the short-password warning threshold is 16 chars — if your
current password is shorter, users will see a warn on startup but
won't be blocked.

**Smoke after merge:**
```bash
bun test gateway/tests/auth-hardening.test.ts
# Expected: 11 pass, 0 fail.
# Manual: start the gateway with WINDYFLY_ENV=production and empty
#         DASHBOARD_PASSWORD. Expected: process exits immediately
#         with a clear error. Unset WINDYFLY_ENV, retry: starts
#         with a warning on stderr.
```

---

## #9 — Fix P1-E1+E2: canonicalize ETERNITAS_URL + derive identity from JWT

**Why high-risk:** Touches identity resolution at hatch time. Adds
JWT-claim reading (unsigned — we trust the token because we got it
from the account-server, not because we verify it here). New
deprecation warning on `ETERNITAS_API_URL`.

**Needs eyes on:** `identity_from_jwt` does NOT verify the JWT
signature — it only reads claims. Confirm this is acceptable for
your use case (the token's already been through account-server
auth by the time we read its claims). Also: the "fail loud" branch
in `_step_link_passport` appends to `result.errors` when `WINDY_JWT`
is present but no identity claim is extractable. This changes hatch
output from "silently skip" to "loud error in result.errors" —
verify your downstream logging handles the new surface.

**Smoke after merge:**
```bash
pytest tests/contract/test_eternitas_plumbing.py -q
# Expected: 16 pass, 0 fail.
# Manual: run `windy go` with WINDY_JWT set to a token with a `sub`
#         claim. Expected: passport link-back happens against the
#         claim's sub value.
```

---

## #12 — Fix P1-O5 + P2-S7 + P2-S8: rate-limit buckets + LRU cap + strict CORS

**Why high-risk:** CORS allow-list becomes strict (unknown origins
get no ACAO header, not the old fallback). Rate-limit map grows LRU
cap at 50k entries. New `chat` and `upstream` buckets gate
`/api/chat` and `/api/providers/validate` respectively. **Conflicts
with #8** on the rate-limit struct — resolve per the playbook (hand-
union the bucket types: `setup | auth | chat | upstream`).

**Needs eyes on:** the `chat` bucket at 60/min/IP — confirm that's
generous enough for your heaviest legitimate user. If you have
users behind a single NAT (office, school), they share an IP; 60
requests between them per minute is 1/sec cumulative. The
`upstream` bucket at 30/min gates OpenAI/Anthropic key validation;
30 attempts is above normal but below abuse.

**Smoke after merge:**
```bash
bun test gateway/tests/rate-limits-cors.test.ts
# Expected: 9 pass, 0 fail.
# Manual: curl -H 'Origin: https://evil.com' http://localhost:3000/api/health
#         Expected: no Access-Control-Allow-Origin header in the response.
```

---

## #13 — Fix P1-E3+E4: wk_ key minted before cloud quota; doc shared JWKS

**Why high-risk:** Changes hatch ordering — adds a new
`_step_mint_bot_key` step between link-passport and the concurrent
fan-out. Cloud-quota call now uses the `wk_` bot key when available,
falling back to owner JWT otherwise. Doc-only note on
`link_passport_with_identity` about the Pro/Cloud shared-JWKS
coupling.

**Needs eyes on:** the new step fails soft (appends to `result.errors`)
rather than blocking the hatch. Verify that's what you want for a
first-ever hatch — a failed wk_ mint means all downstream ecosystem
calls during the hatch use the owner JWT (degraded but functional).

**Smoke after merge:**
```bash
pytest tests/contract/test_hatch_ordering.py -q
# Expected: 8 pass, 0 fail.
# Manual: run `windy go` with WINDY_JWT set. Expected log line:
#         "Hatch: minted wk_ bot key <key_id> (scopes=mail:send,…)"
#         between the Eternitas and matrix/mail/phone steps.
```
