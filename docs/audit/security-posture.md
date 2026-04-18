# Security Posture — Windy Fly

Static + probe audit, 2026-04-17.

## 1. Auth surface

| Surface | Where | Gate | Verdict |
|---|---|---|---|
| Gateway HTTP API (Bun) | `gateway/src/server.ts` | `DASHBOARD_PASSWORD` bearer / cookie / *query param* | **Broken** — see P0-S1, P0-S2 |
| Ecosystem outbound (agent → Pro/Mail/Cloud) | `src/windyfly/auth/bot_credentials.py` | `wk_` bot key, minted once per owner JWT | OK |
| Trust gate (sensitive actions) | `src/windyfly/trust/gate.py` | `require_trust(action)` | **Partial** — bypassed by `/run`/`/git` slash commands (P0-S3) |
| Command registry (slash commands) | `src/windyfly/commands/registry.py` | None. `category="12_developer"` is display-only | **Broken** — see P0-S3 |
| UDS bridge (Bun → Python) | `src/windyfly/bridge/uds_server.py` | Filesystem socket permissions | OK on user machine; **missing on VPS** (socket bound to default perms) |

## 2. CORS

Gateway: `gateway/src/server.ts:150–163`. Allow-list is:

```
https://windyword.ai
http://localhost:5173
http://localhost:8098
http://localhost:3000
```

**Bug:** when `Origin` is not in the allow-list, the server still sets
`Access-Control-Allow-Origin: https://windyword.ai` (the fallback to
`allowedOrigins[0]`). This isn't exploitable in a modern browser (the
browser rejects the mismatched origin), but it's the wrong behaviour —
the header should be omitted. P3.

## 3. Rate limits

One rate limiter exists: `isRateLimited(ip)` at `server.ts:63`, **only
applied to `/api/setup/*`** at line 949. Every other route — chat,
providers, machines, skills, identity — has no limit. Consequences:

- `/api/providers/validate` (calls OpenAI/Anthropic with user-supplied
  key) can be used as an upstream abuse amplifier.
- `/api/chat` has no limit; a misbehaving attached LLM or a malicious
  client can burn through the owner's token budget.
- The rate-limit map is in-memory, unbounded, never evicted. Memory-DoS
  possible with distributed sources. P3.

## 4. Injection

- **SQL:** one `SELECT` is string-concatenated — `dashboard/data.py`
  uses parameterised queries everywhere I checked. Low risk.
- **Shell:** `/run` and `/git` slash commands pass `shell=True` with
  raw user input. **P0** — see main gap analysis. Two occurrences in
  `src/windyfly/commands/core.py` at :1219 and :1255.
- **SSRF:** `/web` slash command fetches user-supplied URL with
  `follow_redirects=True`, no host allow-list. **P0** — `src/windyfly/
  commands/core.py:1234`. Attacker on any channel can fetch
  `http://169.254.169.254/latest/meta-data/iam/security-credentials/`
  on a VPS-deployed agent.
- **TOML injection:** mitigated by `sanitizeForToml` which strips
  control chars + quotes. OK.
- **XSS:** the login page (`server.ts:130`) ships inline HTML but the
  password is never echoed back; no reflection point found.

## 5. JWT / signature verification

- Outbound `wk_` mint: we use the owner's JWT as a Bearer — verification
  is the account-server's responsibility.
- Inbound from Eternitas: `trust.changed` webhook payload is parsed
  **without signature verification** (`src/windyfly/trust/webhook.py`).
  The Eternitas docs specify dual `X-Eternitas-Signature` HMAC +
  `X-Windy-Signature` detached ES256 JWS. Neither is checked. **P0.**

## 6. Open redirects

- `gateway/src/server.ts:120` issues a 302 to
  `url.pathname + url.search` after stripping the `auth` query param.
  The target is derived from the current request's own URL, not from
  user input, so it's not an open redirect.

## 7. Timing attacks

- `authHeader === \`Bearer ${DASHBOARD_PASSWORD}\`` at `server.ts:111`
  uses `===`. Node's string `===` is not constant-time. On a VPS
  with no rate limit on auth checks, a remote attacker can brute-force
  the password via timing. **P1.**

## 8. Secret exposure

- Scanned `git log --all -p` for committed `.env` — none found (it's
  gitignored).
- `.env` on disk contains a live Kimi API key; acceptable for local
  dev, would not survive `git add .` accidentally because of
  `.gitignore`. Low risk.
- Dashboard password accepted as `?auth=<password>` URL param
  (`server.ts:117`). Ends up in:
  - nginx / ALB access logs
  - browser history
  - Referrer headers for any outbound request on that page
  **P0** for VPS — mitigation: kill the query-param path, or
  regenerate the password after first login.
