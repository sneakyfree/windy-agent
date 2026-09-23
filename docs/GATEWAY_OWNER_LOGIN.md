# Gateway dashboard: owner sign-in with Windy (SSO #13)

The gateway dashboard no longer has a shared password. It admits exactly one
person, the agent's **owner**, proven by their own Windy login: a hub
(`account.windyword.ai`) RS256 JWT whose `windy_identity_id` equals the
`WINDY_IDENTITY_ID` recorded at hatch.

## Ways in

| Client | How |
|---|---|
| Browser on the agent's machine | Open `http://127.0.0.1:<port>/` → **Sign in with Windy** → loopback + PKCE (S256) against the hub → session cookie |
| API / desktop app | `Authorization: Bearer <hub JWT>` |
| Local dev (non-production, direct loopback, no proxy headers) | Loopback bypass, as before |

The hub client is `windy-fly-dashboard` (public, PKCE S256,
authorization_code + refresh_token). Loopback redirect URIs **must be IP
literals** (`http://127.0.0.1:{port}/api/auth/hub/callback` or
`http://[::1]:{port}/…`); the hub refuses `localhost`. So the gateway builds
the redirect from the loopback literal and the port it bound, never from the
Host header, and bounces `localhost` to `127.0.0.1` first so the session
cookie lands on the callback's origin. A hosted origin (for example
`https://agent.windyword.ai` behind a tunnel) is **off** until the hub
registers it; turn it on with `HUB_OAUTH_PUBLIC_ORIGIN`.

## Who counts as the owner

- Signature: RS256 only, keys from the hub JWKS (cached, re-fetched once on an
  unknown `kid`, rate-limited). HS256 and `none` are refused.
- `iss` ∈ `HUB_ISSUERS` (default `windy-identity,https://account.windyword.ai`).
- `exp` / `nbf` with at most 60 s skew.
- `aud` is checked only when `WINDY_FLY_ENFORCE_AUDIENCE=1` (default off;
  the hub lane flips it once the hub emits `aud`). When on, `aud` must be an
  array containing `windy_fly`.
- Identity: **only** `windy_identity_id` (`HUB_OWNER_CLAIM` to override).
  Never `sub` (it's the hub userId on access tokens but the identity id on
  id_tokens) and never `email` (the hub issues full tokens for unverified
  emails during a 24 h grace window).
- `type` must be `human` when present; agent tokens never open the dashboard.
- `email_verified`: when the claim is present it must be `true`
  (`HUB_REQUIRE_EMAIL_VERIFIED=0` to disable). An absent claim is accepted
  until the hub confirms it always emits it.

## Startup

`WINDYFLY_ENV=production` **refuses to start** without `WINDY_IDENTITY_ID`.
In dev without an owner, the dashboard is reachable only over a direct
loopback connection.

## Linking a chat app (replaces "first sender becomes owner")

The signed-in owner opens **Link a chat app** (`/link.html`), which calls
`POST /api/owner/pair-code` → Python bridge `owner.pair.create`
`{owner_identity, ttl_seconds: 600}` → `{code, expires_at}`. The owner then
sends `/pair <code>` to the agent from Telegram, Signal or another app, and
that sender is bound as the owner on that platform.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `WINDY_IDENTITY_ID` | — | Owner's `windy_identity_id` (required in production) |
| `HUB_JWKS_URL` | `https://account.windyword.ai/.well-known/jwks.json` | Hub signing keys |
| `HUB_ISSUERS` | `windy-identity,https://account.windyword.ai` | Accepted `iss` values |
| `WINDY_FLY_ENFORCE_AUDIENCE` | off | Require `windy_fly` ∈ `aud` |
| `HUB_OWNER_CLAIM` | `windy_identity_id` | Claim holding the owner identity |
| `HUB_REQUIRE_EMAIL_VERIFIED` | on | Refuse `email_verified: false` |
| `HUB_OAUTH_CLIENT_ID` | `windy-fly-dashboard` | Hub OAuth client |
| `HUB_OAUTH_AUTHORIZE_URL` / `HUB_OAUTH_TOKEN_URL` | hub `/api/v1/oauth/{authorize,token}` | Endpoints |
| `HUB_OAUTH_PUBLIC_ORIGIN` | unset (loopback only) | Hosted origin, once the hub registers its callback |
| `GATEWAY_HOST` | `0.0.0.0` | Bind address; set `127.0.0.1` behind a tunnel |
| `GATEWAY_PORT` | `3000` | Bind port |

## Trust webhooks (#5)

`POST /api/webhooks/trust` stays unauthenticated at the HTTP layer (the body
is HMAC-signed and verified in Python against `ETERNITAS_WEBHOOK_SECRET`) and
is capped at 64 KB (413 above that) so an unauthenticated sender can't make
the gateway buffer large bodies.
