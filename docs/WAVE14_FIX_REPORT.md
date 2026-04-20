# Wave 14 — Launch-blocker fixes for `fly.windyword.ai`

**Date:** 2026-04-19 (overnight)
**Source:** `docs/SMOKE_REPORT_2026-04-19.md` — 2 P0 + 2 P1 findings
**Repo:** `sneakyfree/windy-agent` (default branch: `master`)

---

## TL;DR for Grant

Three PRs merged overnight close every P0 and P1 flagged in the smoke report. The gateway on `fly.windyword.ai` is **still running the vulnerable code** — no redeploy was executed per your "Don't redeploy" instruction. All 4 launch-blockers ship the moment you `systemctl restart windyfly-gateway` on the new commit.

| # | PR | Severity | Landed as | Lines changed |
|---|-----|----------|-----------|-----|
| 1 | [#40](https://github.com/sneakyfree/windy-agent/pull/40) | **P0** — Dashboard auth bypass behind nginx | `099adf0` | +527 / -2 |
| 2 | [#41](https://github.com/sneakyfree/windy-agent/pull/41) | **P0** — Unauthenticated WebSocket upgrade | `a2e782f` | +314 / -27 |
| 3 | [#42](https://github.com/sneakyfree/windy-agent/pull/42) | **P1** — Cookie == password AND no hatch cap | `70fabba` | +687 / -10 |

Test suite: **88 → 119 passing / 0 failing** across the 3 PRs.

---

## Redeploy playbook (when Grant wakes up)

```bash
# SSH to the gateway
ssh -i ~/windy-prod-key.pem ubuntu@174.129.41.59

# Pull the latest master
sudo -u root -H bash -c 'cd /opt/windyfly && git fetch && git reset --hard origin/master'
sudo -u root -H bash -c 'cd /opt/windyfly/gateway && /opt/bun/bin/bun install --frozen-lockfile'

# Restart the service — also clears the stale "Pro returned 404" log line
# from broker-verify (see SMOKE_REPORT §3).
sudo systemctl restart windyfly-gateway
sleep 2
sudo systemctl status windyfly-gateway --no-pager | head -10
```

### Post-redeploy smoke

Re-run the subset of `SMOKE_REPORT_2026-04-19.md` that covers the 4 fixed bugs:

```bash
# §2 — dashboard auth. Without a cookie, root must be 401.
curl -sS -D - -o /dev/null https://fly.windyword.ai/ | head -1
# Expect: HTTP/1.1 401 Unauthorized   (pre-fix was 200)

curl -sS -D - -o /dev/null https://fly.windyword.ai/api/sliders | head -1
# Expect: HTTP/1.1 401 Unauthorized   (pre-fix was 200)

# §2 — session cookie is NOT the password.
PW=$(grep '^DASHBOARD_PASSWORD=' ~/.windyfly-phase5-state | cut -d= -f2-)
curl -sS -i -c /tmp/c -X POST \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "password=$PW" \
  https://fly.windyword.ai/api/auth/login | grep -i set-cookie
# Expect: Set-Cookie: windy_auth=<~43-char base64url token>; ...
# NOT the DASHBOARD_PASSWORD value.

# §7 — WS must 401.
python3 -c "
import asyncio, ssl, certifi, websockets
async def go():
    try:
        async with websockets.connect('wss://fly.windyword.ai/ws/chat', ssl=ssl.create_default_context(cafile=certifi.where())) as ws:
            print('❌ still open without auth')
    except Exception as e:
        print(f'✅ rejected: {e}')
asyncio.run(go())"
# Expect: ✅ rejected: server rejected WebSocket connection: HTTP 401

# §4 — hatch concurrency cap. Requires a valid bk_live_ token; blocked
# today on Pro's /credentials/issue returning 500. Document for later.
```

---

## What each PR changed

### PR #40 — Dashboard auth bypass (SMOKE_REPORT §2)

**Root cause:** `isLocalhostRequest` trusts `server.requestIP(req).address`. Under the production `nginx → Bun on 127.0.0.1:3000` topology, every public request arrives from `127.0.0.1`. The auth gate at `server.ts:297` (pre-fix) was therefore skipped on every proxied request.

**Fix:** new `shouldBypassAuthForLocalhost(req, server, env)` helper:
- In production → never bypass.
- In dev → only bypass if the request has no `X-Forwarded-For` / `X-Real-IP` header (proof that no proxy was inserted).

**Tests:** 10 new in `tests/dashboard-auth-proxy.test.ts` — decision-table (production vs dev × XFF-present vs absent × loopback vs public peer × Host-spoof trap) + source regression guard.

### PR #41 — WebSocket unauth upgrade (SMOKE_REPORT §7)

**Root cause:** `fetch()` handler at `server.ts:~1550` short-circuited on `/ws/chat`, `/ws/terminal/:id`, `/ws/machine/:id` by calling `server.upgrade()` before ever running `handleRequest` (where `checkDashboardAuth` lives). `/ws/terminal/:id` then auto-emits `pty:create` on open. The moment any real `machineId` became known (leak, log, brute-force, or just `/api/machines` listing it), anyone on the internet could attach a PTY.

**Fix:**
- Extracted `isDashboardAuthValid(req, server)` from `checkDashboardAuth` so both gates share one decision.
- WS upgrade paths now fail-closed with 401 + `WWW-Authenticate: Bearer realm="..."` before `server.upgrade()`.
- Failed WS auth probes consume the `auth` rate-limit bucket (5/min/IP), matching the brute-force-login throttle.

**Tests:** 12 new in `tests/ws-auth.test.ts`:
- 6 `isDashboardAuthValid` decision cases (some skip when `DASHBOARD_PASSWORD` is unset locally — intentional).
- 4 **live** `Bun.serve` integration tests that attempt real HTTP upgrades and assert 401 back, no 101 Switching Protocols.
- 2 source regression guards (auth precedes `server.upgrade`; failed auth touches `auth` bucket).

### PR #42 — Opaque session cookie + bounded hatch concurrency (SMOKE_REPORT §2, §4)

**Two batched P1 fixes**:

**(a) Session cookie.** Login now mints a random 256-bit `base64url` token stored server-side for 24 h. The cookie value is opaque; the master `DASHBOARD_PASSWORD` never enters a `Set-Cookie` header. New helpers: `createDashboardSession`, `isValidDashboardSession`, `revokeDashboardSession`. Memory-only store with a 10k hard cap + opportunistic pruning.

**(b) Hatch concurrency.** New `gateway/src/hatch-concurrency.ts`:
- Global cap `MAX_CONCURRENT_HATCHES=3` (env-tunable).
- Per-IP cap `MAX_HATCHES_PER_IP=2`.
- Slot acquisition happens AFTER broker-verify, so unauth callers can't consume capacity.
- `startHatchRemoteSse.cancel()` now `SIGTERM`s the Python subprocess (pre-fix kept it running → survived client disconnect) AND releases the slot.
- Release also fires in the normal `finally` of the stream.

**Tests:** 21 new across `tests/session-cookie.test.ts` + `tests/hatch-concurrency.test.ts` — mint opacity, TTL expiry + eviction, revoke, cap enforcement, slot release on normal completion AND client cancel, subprocess kill on cancel, source-level ordering guards.

---

## What didn't change (but Grant might ask about)

### P1/P2 items deferred by scope

The smoke report lists 6 P2 + 5 P3 findings that were **intentionally out of scope** for this overnight pass:

- **P2: `/api/webhooks/trust` 500s** (no Python brain on this box). Either stand up a brain or short-circuit to 503. Small change, worth doing next.
- **P2: ~90% of the proxy route surface is dead code on this deploy.** Consider a `server-gateway.ts` build that ships only `/`, `/api/health`, `/hatch/remote`, `/api/webhooks/trust`, `/api/auth/login`. Attack surface cleanup.
- **P2: no broker-verify cache.** Pro round-trip on every hatch. At current traffic it's a non-issue; becomes one if a viral Grandma-Ribbon moment hits 100 hatches/min.
- **P2: SENTRY_DSN is empty.** Populate in `/etc/windyfly/production.env` and restart — clears the stale `Pro returned 404` log line at the same time.
- **P2: bridge reconnect gives up at 20 attempts.** Fix or remove the cap.
- **P3: no security headers.** Add HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP at the nginx layer. Low effort, high signal.
- **P3: EBS vol `vol-0868022668d89d0a5` missing Project/Product tags.** One `aws ec2 create-tags` away.
- **P3: No success-path log for broker-verify.** Add structured log at end of `verifyBrokerToken`.
- **P3: `/does-not-exist` falls through to SPA index.html.** Return 404 for unknown non-asset paths.

### End-to-end hatch ceremony still unverifiable

Smoke §3 ⚠ note: Pro's `/api/v1/agent/credentials/issue` returns HTTP 500 `broker_issue_failed` for synthetic `windy_identity_id` values. The gateway-side gate is correct; the Pro-side issue flow appears to require a pre-existing Windy identity row. Minting-and-using a real token end-to-end remains blocked on a Pro-side PR — unchanged by this wave.

---

## Stats

```
3 PRs merged (admin-squash per CLAUDE.md Wave 12 playbook — chronic CI runner-pickup)
1 branch remaining to merge: wave14-fix-report (this file)

Files touched:
  gateway/src/server.ts                      (+167 / -29)
  gateway/src/hatch-remote.ts                (+76 / -6)
  gateway/src/hatch-concurrency.ts           NEW (+106)

  gateway/tests/dashboard-auth-proxy.test.ts NEW (+105)
  gateway/tests/ws-auth.test.ts              NEW (+231)
  gateway/tests/session-cookie.test.ts       NEW (+126)
  gateway/tests/hatch-concurrency.test.ts    NEW (+279)

  docs/SMOKE_REPORT_2026-04-19.md            NEW (+382)
  docs/WAVE14_FIX_REPORT.md                  NEW (this file)

Test count: 78 → 119 passing (+41), 2 skip (DASHBOARD_PASSWORD-dependent), 0 fail.
```
