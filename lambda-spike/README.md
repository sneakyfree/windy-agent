# Phase A M1 Spike — Lambda Cloud Runtime

**Status:** 🚧 Day-1 scaffold landing 2026-05-08 evening.
**Owner:** Kit Zero. **Authorized:** Grant 2026-05-08 ("concur all").
**Companion to:** [`~/kit-army-config/docs/phase-a-cloud-runtime-plan-2026-05-08.md`](https://github.com/sneakyfree/kit-army-config/blob/main/docs/phase-a-cloud-runtime-plan-2026-05-08.md), [ADR-008](https://github.com/sneakyfree/kit-army-config/blob/main/docs/adr-008-fly-runtime-architecture-2026-05-08.md).

---

## Goal

Prove the wire protocol: Lambda can host a Windy Fly agent loop, with per-user SQLite state on S3, with sub-second cold-start latency, and respond to chat messages matching the wire protocol the SPA already speaks via `account-server/src/routes/fly.ts`.

If this spike succeeds, M2-M7 (multi-tenant, channel routing, cost guards, beta migration) follow. If the spike fails (cold-start >1s, packaging too large, S3 round-trip too slow), pivot to Fargate-based long-running tasks per ADR-008 fallback.

## What this is NOT

- **NOT** a production-grade Windy Fly. It's a focused proof-of-architecture.
- **NOT** importing the full `windyfly` package. M1 uses a slim handler + raw Anthropic API call (no SDK, no channels, no skills, no memory beyond raw SQLite).
- **NOT** the multi-tenant scale-out solution. M1 is single-tenant. M2 adds per-user spawn isolation.

The point is to validate the *architecture*, not the *features*. M2 swaps in real windyfly code once the spike validates that Lambda can host the loop.

## Architecture

```
SPA / Mobile / Electron
      ↓ POST {message, user_id}
account-server/src/routes/fly.ts
      ↓ proxy to WINDYFLY_GATEWAY_URL
[future API Gateway] → Lambda
      │
      ├─ S3: download s3://<bucket>/users/<user_id>/state.db → /tmp
      │     (NoSuchKey → init fresh schema)
      │
      ├─ append user message to SQLite
      │
      ├─ load last 20 turns as Anthropic message format
      │
      ├─ POST https://api.anthropic.com/v1/messages
      │     (raw urllib, no SDK to keep package slim)
      │
      ├─ append assistant response to SQLite
      │
      └─ S3: upload /tmp/state.db → s3://<bucket>/users/<user_id>/state.db
            ↓
      Return {response, cold_start, timing_ms: {...}}
```

## Files

- `lambda_handler.py` — entry point. Slim, ~200 LOC. Handles the full event-load-process-persist-respond cycle.
- `deploy.sh` — packages + uploads to AWS Lambda. Reads CLOUDFLARE-style ENV for AWS auth (uses lockbox `windy-ecosystem-admin` keys).
- `README.md` — this file.

## Day-1 deliverable (committed 2026-05-08 evening)

✅ `lambda_handler.py` written
✅ Wire protocol matches `fly.ts` (event shape `{message, user_id}` → response `{response, cold_start, timing_ms}`)
✅ S3 round-trip + SQLite per-user state implemented
✅ Anthropic API call via raw urllib (no SDK bloat)
✅ Cold-start tracking via module-level flag
🚧 `deploy.sh` — being written next
🚧 AWS resources (Lambda function + S3 bucket + IAM role) — provisioning next
🚧 First end-to-end smoke test — needs deploy

## Day-2 plan (next session)

1. Provision AWS resources via API:
   - S3 bucket `windyfly-cloud-runtime-state-dev` (us-east-1, versioned, lifecycle: 30-day non-current expiration)
   - IAM role for Lambda with: S3 read+write on the bucket, basic CloudWatch logs
   - Lambda function `windyfly-runtime-spike-dev` with Python 3.12 runtime, 512MB memory, 30s timeout
2. Package + upload Lambda zip
3. End-to-end smoke: invoke with `{message: "hello agent", user_id: "test-user-001"}`, verify Anthropic responds, verify state persists to S3
4. Cold-start measurement: 5 cold invocations, p50 latency
5. Multi-turn smoke: 5 messages in same user_id, verify history loads correctly
6. Document findings in `kit-army-config/docs/adr-009-m1-tracker.md` companion (Phase A M1 tracker — to be created)

## Day-3+ plan

If day-2 smoke passes:
- M1 sign-off → proceed to M2 (multi-tenant spawn isolation)
- Update Phase A plan doc with measured cold-start numbers
- Begin M2: per-user Lambda spawn, S3 strong-consistency lock pattern, secrets-manager integration

If day-2 smoke fails:
- Document the failure mode in detail
- Pivot per ADR-008: Fargate long-running task with auto-suspend
- Re-spike on Fargate

## Environment variables expected at runtime

- `AGENT_STATE_BUCKET` — S3 bucket for state files. Default `windyfly-cloud-runtime-state-dev`.
- `ANTHROPIC_API_KEY` — your Anthropic API key. Lockbox §Anthropic.
- `WINDYFLY_MODEL` — Claude model. Default `claude-sonnet-4-5-20250929`.

## Cross-references

- [Phase A cloud-runtime plan](https://github.com/sneakyfree/kit-army-config/blob/main/docs/phase-a-cloud-runtime-plan-2026-05-08.md) — full M1-M7 milestone breakdown
- [ADR-008](https://github.com/sneakyfree/kit-army-config/blob/main/docs/adr-008-fly-runtime-architecture-2026-05-08.md) — the architectural decision that authorizes Phase A
- [account-server/src/routes/fly.ts](https://github.com/sneakyfree/windy-pro/blob/main/account-server/src/routes/fly.ts) — what proxies HTTP requests to the agent runtime today (placeholder; will route to this Lambda once deployed)
