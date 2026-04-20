# White-Glove Smoke Prompt — windy-agent (Windy Fly gateway)

**Created:** 2026-04-19, after Wave 13 Phase 5 deploy to AWS
**Purpose:** Hand to a fresh Claude session to do industrial-grade smoke testing on the deployed Fly gateway at `https://fly.windyword.ai`.

---

## Why this prompt exists

Wave 13 Phase 5 shipped the windyfly-gateway to AWS. It's the public surface for Windy Fly — proxies the Trust Dashboard, validates broker tokens issued by Pro's `/api/v1/agent/credentials/issue`, runs the `hatch-remote` SSE ceremony, and bridges to the local agent runtime via Unix domain socket. None of this has been clicked through against the deployed surface. SSE streams, WebSocket chat, and broker-token verification all have nuanced failure modes that pass unit tests and break in prod.

---

## Paste this to a fresh Claude session

> You are doing **industrial-grade white-glove smoke testing** on the production windyfly-gateway, freshly deployed to AWS as Wave 13 Phase 5 at `https://fly.windyword.ai`. Your job is to find every defect a real Fly user OR a token-forger would hit. Unit tests do NOT count — only behaviour observed against the live URL.
>
> **Read first:**
>
> 1. `~/.claude/projects/-Users-thewindstorm/memory/MEMORY.md` (auto-loaded)
> 2. `/tmp/kit-army-config/ACCESS_LOCKBOX.md` — search "Wave 13" + "Fly" / "windy-agent"; gives you live URL `fly.windyword.ai`, EC2 instance, EIP, BROKER_HMAC_SECRET, DASHBOARD_PASSWORD, agent service token.
> 3. `windy-agent/docs/WAVE13_PHASE5_RUNBOOK.md` — what Phase 5 actually shipped (gates, rollback procedures, cost model).
> 4. `windy-agent/gateway/src/server.ts` — route table is in the file header docstring (~30 routes covering dashboard, sliders, cost, intents, memory search, personality history/snapshot/drift/rollback, skills CRUD/evaluate/promote/rollback/golden-tests/regression, decay, conflicts, moments, failures, mode, offline, events, WS chat, hatch-remote SSE).
> 5. `windy-agent/gateway/src/broker-verify.ts` — Pro contract for broker token validation.
> 6. `windy-agent/gateway/src/hatch-remote.ts` — SSE ceremony endpoint.
>
> **Then do all of the following against `https://fly.windyword.ai`:**
>
> ### 1. Public surface
> - `GET /` → Trust Dashboard HTML loads? CSS + JS assets resolve?
> - `GET /api/health` → `{status:"ok"}` with 200?
> - Send malformed JSON, wrong content-type, empty body, 10MB body to a POST → clean 400, never 500 with Bun stack trace.
> - `GET /api/<random-nonexistent>` → clean 404.
> - Inspect the HTML for any leaked dev URLs (`localhost`, `127.0.0.1`, dev tokens).
>
> ### 2. Trust Dashboard auth
> - Try to load `GET /` without auth — does the dashboard render or redirect to login?
> - The dashboard uses a `DASHBOARD_PASSWORD` (lockbox). Log in. Verify session persists.
> - Wrong password N times → does the `auth` rate-limit bucket kick in (5/min/IP per `server.ts` constants)?
>
> ### 3. Broker token verification — the Pro contract
> - Pro's `/api/v1/agent/credentials/issue` mints `bk_live_*` tokens. Fetch one (sign Pro request with `BROKER_HMAC_SECRET` from lockbox).
> - Use that broker token to call any token-gated endpoint here on Fly. Verify Fly accepts it (it must call back to Pro's `/api/v1/agent/credentials/verify` to validate).
> - Use a **forged** broker token (random `bk_live_*` string) → 401, and check that Fly's verify cache doesn't accidentally treat unknown tokens as valid.
> - Use an **expired** broker token → 401.
> - Use a token that Pro returns `{ok:false, reason:"not_found"}` for → 401.
> - Verify that `broker-verify.ts`'s 5-min cache works: hit twice in succession, second call should not round-trip to Pro.
>
> ### 4. Hatch-remote SSE ceremony
> - `POST /hatch/remote` with a valid broker token → SSE stream opens? Frames flow? Final frame is the agent ID?
> - `POST /hatch/remote` with no token → 400 or 401, never 500.
> - Open SSE stream, then **disconnect mid-stream** — does the gateway clean up the subprocess? (Tail logs to verify no zombie Python process.)
> - Open 5 concurrent hatch-remote streams from the same IP → do they all work or hit a per-IP cap?
> - Verify the SSE response has `Content-Type: text/event-stream` and proper buffering disabled (nginx config `proxy_buffering off`).
>
> ### 5. UDS proxy endpoints — sliders, cost, intents, dashboard, memory
> - For each endpoint in the server.ts route docstring (sliders, cost.daily, intents.list, dashboard.summary, memory.search), call it with a valid token, verify response shape matches what the local agent runtime expects.
> - These all proxy to a Unix domain socket on the gateway host. If the local agent runtime isn't running on the EC2 instance (which it might not be — the runbook says "Agent runtime continues to live on user machines"), these endpoints should return a clear "agent not running" error, NOT 500.
> - **Investigate the architecture:** if the gateway hosts no agent runtime, what's the point of these proxy endpoints on the deployed instance? Are they meant for a different topology? Is this dead code in production? Document.
>
> ### 6. Personality + skills CRUD
> - `GET /api/personality/history`, `POST /api/personality/snapshot`, `GET /api/personality/drift`, `POST /api/personality/rollback` — exercise each. Same caveat as §5.
> - `GET /api/skills`, `POST /api/skills`, `POST /api/skills/:id/evaluate`, `POST /api/skills/:id/promote`, `POST /api/skills/:id/rollback`, `POST /api/skills/:id/golden-tests`, `POST /api/skills/regression` — exercise each.
> - Try to access another user's skills — must 403, no cross-tenant leak.
>
> ### 7. WebSocket chat
> - Open `WS /ws/chat` with a valid broker token in the connection params/header.
> - Send a chat message. Receive response. Verify it streams.
> - Send 100 messages rapidly — does the `chat` rate-limit bucket (60/min/IP) kick in?
> - Open WS without auth → connection should be rejected, not silently dropped.
> - Force-disconnect mid-stream → server cleans up.
>
> ### 8. Rate limiting — verify the bucketed limits actually work
> - Per `server.ts`: `setup=10/min`, `auth=5/min`, `chat=60/min`, `upstream=30/min`. Hit each bucket past its limit. Verify 429 with `Retry-After` header.
> - Verify a flood on one bucket doesn't drain another's budget.
>
> ### 9. CORS, headers, TLS
> - OPTIONS from disallowed origin → no `*`.
> - HSTS, X-Content-Type-Options, X-Frame-Options, CSP all present and tight (this is a public surface for code-execution agents — CSP matters).
> - SSL Labs grade ≥ A.
> - Cert: valid, not self-signed, covers `fly.windyword.ai`, served by certbot/letsencrypt per the runbook.
>
> ### 10. Cross-service contract
> - Verify the gateway actually hits Pro's `/credentials/verify` (not just trusting tokens locally). SSH the EC2 instance, tail the gateway logs, check for outbound HTTPS to `pro.windyword.ai` or wherever Pro lives.
> - When verify call fails (e.g. Pro is down), what does the gateway do? Cached approval? Hard fail? Document observed behaviour.
>
> ### 11. Production observability
> - Tail the systemd journal for `windyfly-gateway.service` (lockbox has SSH command). ERROR/WARN must be explainable.
> - Check Sentry if `SENTRY_DSN` is configured — any unexpected exceptions reported?
> - Check the SSE / WS connection counts over time — any leaks?
>
> ### 12. Cost discipline
> - The runbook says ~$15/mo (t3.small + EIP). Verify the EC2 instance is t3.small, not larger.
> - Verify CloudWatch / billing isn't surprising.
> - Verify there's no orphaned EBS volume / snapshot from prior failed deploys.
>
> ---
>
> **Output format:** Single Markdown report at `windy-agent/docs/SMOKE_REPORT_<YYYY-MM-DD>.md`. One H2 per section. Bug format: `**SEVERITY** — title — observed vs expected — repro — fix or "needs investigation"`. Severity: P0 = accepts forged broker tokens / opens shell to attacker / leaks Pro-side data, P1 = breaks hatch ceremony or chat for a user, P2 = ugly nonfatal, P3 = polish.
>
> **What "done" looks like:** zero P0, zero P1.
>
> **Constraints:**
> - Test the deployed URL.
> - SSE/WS connections must be cleaned up after each test — don't leak.
> - Don't fix yet — discovery first.
> - Per branching policy (`windy-agent/CLAUDE.md`): feature branch + PR. Default branch is **`master`**, not `main`.
> - The repo's CI has chronic runner-pickup failures — admin merge per Wave 12 playbook is acceptable.
