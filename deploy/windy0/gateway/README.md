# Windy 0 gateway — deploy package

Runs `gateway/` on Windy 0 as a **loopback-only** systemd user service behind
the existing cloudflared tunnel. Its jobs:

- **Owner dashboard** at `https://windy0-agent.thewindstorm.uk` — sign-in is "Sign in with
  Windy" (hub JWT, PKCE). Only the agent's owner (`WINDY_IDENTITY_ID`) gets in. No
  dashboard password.
- **Owner pairing** — the signed-in owner mints a one-time code and sends
  `/pair XXXX-XXXX` to the agent on Telegram/Signal/etc. (replaces first-sender-is-owner).
- **Eternitas trust webhooks** at `/api/webhooks/trust` (HMAC-verified by the
  Python bridge; production fails closed without `ETERNITAS_WEBHOOK_SECRET`).

The hostname lives in the ops zone (thewindstorm.uk, next to windy0-ssh) on
purpose: it is one person's agent. `agent.windyword.ai` stays free for a real
multi-user agent surface later.

It is **not** the backend for Windy Word's "Talk to agent" for other users. The
gateway fronts ONE agent (Grant's). Pointing Pro's global `WINDYFLY_GATEWAY_URL`
at it would route every user to Grant's agent. Voice dispatch must go to each
user's own roster agent in windy-chat.

## Files

| File | Where it goes |
|---|---|
| `windy-gateway.service` | `~/.config/systemd/user/` — `bun run src/server.ts`, `Restart=always`, waits for `/api/health` before reporting started |
| `windy-gateway-health.{service,timer}` | every 2 min; 2 failed probes → restart + `user.err` log |
| `healthcheck.sh` | used by both units |
| `windy-gateway.env.example` | → `~/.config/windy-gateway.env` (600) |
| `cloudflared-ingress.snippet.yml` | one rule for `/etc/cloudflared/config.yml` (needs sudo) |
| `install.sh` | copies units; `--enable` starts them |

## Go-live checklist (only after Grant says "keep")

1. Prerequisites:
   - The gateway hub-login PR and the Python owner-pairing PR are merged, and the live checkout is pulled.
   - The hub lane has registered the public OAuth client (`HUB_OAUTH_CLIENT_ID`) with the loopback and `https://windy0-agent.thewindstorm.uk/api/auth/hub/callback` redirects.
   - `ETERNITAS_WEBHOOK_SECRET` from Eternitas's `HMAC_WINDY_AGENT` subscriber is in `~/.windy/windy-0.env`, and `windytalk-agent-bridge.service` has been restarted.
2. `bash deploy/windy0/gateway/install.sh`, then fill in `WINDY_IDENTITY_ID` in `~/.config/windy-gateway.env`.
3. `bash deploy/windy0/gateway/install.sh --enable`, then check `curl -s http://127.0.0.1:3000/api/health`.
4. Route it:
   `sudo cloudflared tunnel route dns e2f8bb11-060e-4707-8c57-6852b4c6d24d windy0-agent.thewindstorm.uk`,
   add the snippet above the 404 rule in `/etc/cloudflared/config.yml`, run
   `sudo systemctl restart cloudflared`, then `curl -s https://windy0-agent.thewindstorm.uk/api/health`.
5. Tell the hub lane the webhook URL: `https://windy0-agent.thewindstorm.uk/api/webhooks/trust`.
6. Tell lane 80 to add `https://windy0-agent.thewindstorm.uk/api/health` to the uptime checks.

## Rollback

`systemctl --user disable --now windy-gateway.service windy-gateway-health.timer`,
remove the ingress rule, then `sudo systemctl restart cloudflared`. The agent itself
(`windy-0@*`) never depends on the gateway.
