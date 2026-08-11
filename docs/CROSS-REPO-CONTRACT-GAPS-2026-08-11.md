# Cross-repo contract gaps found from the windy-agent side — 2026-08-11

**Filed by the "Windy Agent 1" session while repairing windy-agent's client contracts
(branch `fix/client-contract-repairs`, working tree only, nothing pushed).**

Everything below was found by reading the *other* side of a contract windy-agent speaks.
**None of it was fixed here** — each item belongs to a repo another session owns. This file
exists so the findings reach those owners instead of dying in a session transcript.

Dated diagnostic. Re-verify before acting; tombstone when the table is closed.

---

## How to read this

windy-agent talks to windy-pro's account-server on four contracts. **Three of the four were
wrong**, and every one of them failed *silently* — the errors were caught, logged at a level
nobody reads, and reported upward as success. The repairs on windy-agent's side are done. The
items below cannot be closed from this repo.

---

## 🔴 P0 SECURITY — any signed-in user can revoke any agent's credentials

**Found while repairing `revoke_bot_key`; verified twice, independently, by reading windy-pro
source directly. This is an authorization gap, not a contract mismatch, and it is the most
serious item in this file.**

Two routes in `windy-pro/account-server/src/routes/identity.ts` carry `authenticateToken` and
**no ownership check whatsoever**, and they chain:

1. **`GET /api/v1/identity/api-keys?identityId=<anything>`** (`:1226-1234`) reads `identityId`
   straight from the query string and returns **every key row for that bot** — including each
   key's `id`. Nothing verifies the caller has any relationship to that identity.
2. **`DELETE /api/v1/identity/api-keys/:keyId`** (`:1220`) calls
   `revokeBotApiKey(keyId, revokedBy)` (`account-server/src/identity-service.ts:542-550`), which
   looks the key up **by id alone**, revokes it, and uses `revokedBy` only for the audit-log
   entry. There is no operator check, no admin check, no 403 path.

So any authenticated Windy Word account can enumerate another person's agent's key ids and
revoke them. **The contrast proves it is an oversight, not a design:** the *create* route
directly above (`POST /api-keys`, `:1175-1218`) explicitly resolves the passport's
`operator_identity_id`, checks `isAdmin || isOperator`, and 403s otherwise. The read and delete
paths simply never got the same treatment.

**Why this matters now, concretely:** at the August bootcamps every attendee is an authenticated
user. As it stands, one attendee can revoke every other attendee's agent credentials, and the
audit log will faithfully record who did it *after* the agents stop working.

**Fix belongs to windy-pro** (one session owns that repo — do not let two sessions edit it):
apply the same operator-or-admin check the create route already implements to both the list and
the delete, and scope the list route to the caller's own identities unless the caller is an
admin. `botAuthLimiter` is also defined at `:1166` and never applied to any of these routes.

---

## 🔴 P0 — the one that would break a real hatch

### 1. windy-pro aborts the hatch after 15 s and reports success

`windy-pro/account-server/src/services/hatch-steps.ts:122-135` — `fetchJson` calls
`await resp.json()` under `AbortSignal.timeout(15_000)`, but `POST /hatch/remote` answers with
`text/event-stream` that runs for **minutes**. The `AbortError` is swallowed by the inner
`catch { /* non-JSON body */ }`, so account-server logs `ok: true, http_status: 200` — while the
aborted connection triggers the gateway's `cancel()` and **SIGTERMs the Python hatch
mid-ceremony**.

**Consequence:** the moment anyone arms `WINDY_AGENT_URL`, every browser hatch is killed partway
through and reported to the user as a success. This is strictly worse than the current state,
where step 4 refuses honestly and does nothing.

**Fix belongs to windy-pro.** Either give the gateway an `Accept: application/json` → `202
{agent_id}` acknowledgement mode, or make account-server a real SSE consumer. **This must land
before `WINDY_AGENT_URL` is ever set** — add it to the arming checklist in
`00-THE-CONTRACT.md`.

---

## 🟠 P1 — blocks agent identity working end to end

### 2. No owner JWT crosses the handoff, so the remote lane can never mint a bot key

windy-agent's `wk_` bot-key mint needs `WINDY_JWT`. The `/hatch/remote` payload
(`hatch-steps.ts:425-445`) never carries one. The gateway now correctly forwards
`bot_identity_id` (repaired here today), but that is **necessary and not sufficient** — the
remote lane will still log `wk_ mint skipped (no WINDY_JWT — offline hatch)`.

**Decision needed (windy-pro + hallway custodian):** add an owner-scoped token to the handoff
contract, or expose a broker-scoped mint path that authenticates with the `bk_live_*` token the
handoff already sends. Until then, remotely-hatched agents keep acting on their **owner's**
credentials — the Principle #6 gap the audits flagged.

### 3. There is no passport → bot-identity mapping anywhere

`POST /api/v1/identity/api-keys` requires the **bot's** `identityId` and 400s on anything else.
No windy-pro endpoint maps a passport to it (`/owns-passport/:passport` returns only the
caller's own id). The closest surface is `GET /api/v1/identity/chat/profile`, which returns
`operated_agents[{matrix_user_id, bot_identity_id}]` — but only for agents that already have an
active `windy_chat` row, and it is not keyed by passport.

**Consequence:** a terminal-lane (`windy go`) agent has **no windy-pro identity at all** — it
exists in Eternitas only — so it can never hold a bot credential no matter what this repo does.
That is the concrete, measurable cost of the terminal door bypassing the hallway, and it is a
custodian-ruling decision, not a client bug.

### 4. windy-pro has no `/link-passport` route, and never has

`windy-agent/src/windyfly/eternitas/provision.py:186` POSTs the passport ↔ identity link to
**both** Pro and Cloud. WindyCloud serves it (`api/app/routes/identity.py`). A search for
`link-passport` across `windy-pro/account-server/src/` returns **nothing** — so the Pro leg has
404'd for its entire life, and the function "never raises."

Repaired here to the extent this repo can: a Pro 404 is now reported as `route_absent`,
distinct from success and from a transport error, and logged at WARNING. **Whether windy-pro
grows the route or windy-agent drops the leg is a cross-repo call.**

---

## 🟡 P2 — noted, not urgent

| # | Gap | Evidence | Owner |
|---|---|---|---|
| 5 | `botAuthLimiter` is defined but **never applied** to `POST /api-keys` — bot key creation is unthrottled | `windy-pro .../routes/identity.ts:1166` vs `:1175` | windy-pro |
| 6 | The server **never downscopes**: it echoes the requested `scopes` back verbatim, so windy-agent's trust-band downscoping handling is defensive-only and band-based scope limiting does not exist server-side | `identity.ts:1209` | windy-pro / Eternitas |
| 7 | Local key revocation **does not propagate** to other platforms — there is no server-side cascade-webhook concept for bot keys at all | measured while repairing `revoke_bot_key` | Eternitas |
| 8 | `hatchResult.data?.host` and `.agent_id` are logged by account-server but the gateway emits neither field, ever — both are always null | `hatch-steps.ts:449-450` | windy-pro or gateway (pick one and make it true) |
| 9 | `MAX_HATCHES_PER_IP` bounds the **entire browser lane**, because every browser hatch arrives from one account-server IP | `gateway/src/hatch-remote.ts` | settle before the gateway is ever deployed |

---

## What was fixed on this side (for the record)

Repaired in windy-agent, working tree only, full suite green (3562 passed / 67 skipped / 0
failed, up from a 3533-test baseline):

- The `wk_` bot-key client now speaks the real `POST /api/v1/identity/api-keys` contract
  (path, `identityId` body, `apiKey`/`expiresAt`/`id` response). It refuses **loudly** when no
  bot identity id is available instead of silently degrading to the owner's JWT.
- The gateway handoff accepts `owner_phone: null` (a phone-less owner used to get a 400 on the
  first real browser hatch), and now carries `bot_identity_id`, `provider` and `model` through
  to the hatch instead of dropping them. `provider` pins the broker token to **one** provider
  env var instead of smearing it across all eight.
- `X-Service-Token` is verified when a secret is configured, and its unverifiable state is
  logged explicitly rather than ignored.
- `GET /api/health` on the gateway now reports `honours_preallocated_passport`, derived from the
  actual adopt-guard code — the R3 pre-requisite that lets a future session **prove** the far
  side honours a handed-down passport before arming `WINDY_AGENT_URL`.
- `MIND_API_URL` / `MIND_BASE_URL` unified behind one resolver, closing a trap where a
  dev/staging override moved the model layer while the runtime claim kept talking to
  **production** Mind.

**Not done here, deliberately:** `WINDY_AGENT_URL` remains unset, the gateway remains
undeployed, and no production system was contacted.
