# Windy Fly — Deployment

This is the launch-prep deployment guide. For the deeper AWS-only
detail (log shipping, IAM policy, webhook scaling) see
[`deploy/aws/FLY_DEPLOYMENT.md`](deploy/aws/FLY_DEPLOYMENT.md) — this
document is the one-stop runbook Grant (or anyone else) should be
able to follow end-to-end for either a self-hosted install or a
managed VPS.

Windy Fly is a **personal agent**, not a shared service. One user,
one agent, one data file. Scale characteristics reflect that: there
are no leader-elected databases, no horizontal replicas, no
session-stickiness concerns. The two supported topologies are:

1. **Self-hosted** — the agent runs on the user's machine. Default.
2. **Managed-VPS** — the agent runs on an EC2 instance the user
   owns. Optional. Useful when the laptop sleeps or travels.

---

## Table of contents

- [1. Self-hosted install](#1-self-hosted-install)
- [2. Managed-VPS install](#2-managed-vps-install)
- [3. Production ecosystem URL config](#3-production-ecosystem-url-config)
- [4. Bot-key rotation schedule](#4-bot-key-rotation-schedule)
- [5. Failure playbook](#5-failure-playbook)

---

## 1. Self-hosted install

The common path. No AWS account, no DNS, no TLS cert — just a machine
that runs Python and Bun.

### 1.1 Prerequisites

- macOS, Linux, or Windows
- [`uv`](https://docs.astral.sh/uv/) (auto-installed by `windy go` if
  missing)
- [`bun`](https://bun.sh) (auto-installed by `windy go` if missing)
- Optional: a Windy Pro account — if present, `windy go` skips the
  "paste an API key" step and uses managed credentials from Pro's
  broker

### 1.2 Install and launch

```bash
pip install windyfly
windy go
```

That's the entire happy path. `windy go` walks the user through:

1. Pre-flight checks (`uv`, `bun`)
2. Pro-credential detection via `~/.windypro/config.json` — silently
   falls through if absent (`--byok` forces the paste-a-key path even
   when a Pro account is paired)
3. Provisioning: Eternitas passport → Windy Mail inbox → Matrix bot
   on Windy Chat → phone number → Windy Cloud quota → birth
   certificate
4. First SMS + welcome email from the newly hatched agent
5. Stack start (brain + gateway), dashboard at http://localhost:3000

After the first run, `windy start` (or a launchd/systemd unit
installed via `windy install-service`) keeps the agent running.

### 1.3 Data locations

| Path                          | Contents                                  |
|-------------------------------|-------------------------------------------|
| `./windyfly.toml`             | Agent config (sliders, model, preset)     |
| `./.env`                      | LLM API keys, ecosystem tokens            |
| `./data/windyfly.db`          | SQLite: memory, episodes, audit log       |
| `./data/birth_certificate*.pdf` | Per-hatch birth certificates            |
| `~/.windypro/config.json`     | Pro account token (read by `windy go`)    |

### 1.4 Upgrade path

```bash
pip install --upgrade windyfly
windy update         # pulls brain/gateway lockfiles, restarts stack
```

---

## 2. Managed-VPS install

For users who want the agent online 24/7 without leaving a laptop
awake. The agent self-deploys to an EC2 instance the user owns — no
Anthropic/Windy-operated infra in the loop.

Baseline: **AWS EC2 `t4g.small`** (Graviton, 2 vCPU, 2 GB RAM,
Amazon Linux 2023). The agent's Python footprint + SQLite + gateway
sits comfortably under 500 MB; the 2 GB buffer is for the
prompt-cache + the occasional LLM request burst. Don't go smaller
than `t4g.small` — `t4g.nano` and `t4g.micro` will OOM the brain on a
multi-turn conversation.

### 2.1 Prerequisites

- AWS account with EC2 + CloudWatch Logs permissions (IAM template
  planned at `deploy/aws/iam-windyfly-runtime.json`; minimums are
  listed in `deploy/aws/FLY_DEPLOYMENT.md` §2.1)
- SSH key pair uploaded to AWS
- Windy Cloud token (easy path) **or** direct AWS credentials

### 2.2 `windy cloud vps-deploy` workflow

The `vps-deploy` command in `src/windyfly/vps_deploy.py` orchestrates
the bring-up. The golden-path flow is:

```bash
# 1. From the local agent that already ran `windy go` successfully:
windy cloud vps-deploy \
    --region us-west-2 \
    --instance-type t4g.small \
    --key-pair my-windy-key

# 2. The CLI provisions the instance, rsyncs the agent's config +
#    SQLite, installs the systemd unit, and starts the service.

# 3. Verify:
windy cloud status          # shows VPS endpoint + health
curl https://<vps>/api/health
```

Under the hood the command:

1. Calls Windy Cloud's `POST /api/v1/vps/provision` (or the AWS API
   directly when `--direct-aws` is set)
2. Waits for the instance to be reachable on port 22
3. `rsync` agent state to `/opt/windyfly/` on the VPS
4. Installs `deploy/systemd/windyfly.service` (below)
5. `systemctl --system start windyfly` and tails logs until
   `/api/health` returns `{"status":"ok"}`

### 2.3 systemd unit file

The canonical unit, installed at `/etc/systemd/system/windyfly.service`:

```ini
[Unit]
Description=Windy Fly personal AI agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=windyfly
Group=windyfly
WorkingDirectory=/opt/windyfly
EnvironmentFile=/etc/windyfly/production.env
ExecStart=/usr/local/bin/uv run python -m windyfly.bridge.uds_server
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

# Security hardening — the agent needs network + its own data dir
# and nothing else.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/windyfly/data /var/log/windyfly
CapabilityBoundingSet=

StandardOutput=append:/var/log/windyfly/brain.log
StandardError=append:/var/log/windyfly/brain.log

[Install]
WantedBy=multi-user.target
```

A matching `windyfly-gateway.service` runs the Bun gateway on port
3000 (fronted by Caddy or nginx for TLS termination; see
`deploy/aws/FLY_DEPLOYMENT.md` §4 for the log-shipping side).

### 2.4 Environment file

`/etc/windyfly/production.env` holds every secret the systemd unit
needs. Copy [`.env.production.example`](.env.production.example) and
fill in values. The production.env **must** be `chmod 0600
windyfly:windyfly` — it contains LLM API keys, the broker signing
secret, and webhook HMAC secrets.

### 2.5 TLS + reverse proxy

The gateway speaks plain HTTP on port 3000. In production, front it
with Caddy (simplest) or nginx + certbot. A minimal `Caddyfile`:

```caddyfile
your-agent.example.com {
  reverse_proxy localhost:3000
}
```

The `/hatch/remote` endpoint accepts remote POSTs from the Pro
Electron app — make sure the reverse proxy is configured to pass
through SSE (`proxy_buffering off` on nginx; Caddy handles it by
default).

---

## 3. Production ecosystem URL config

In production, the agent should point at the hosted ecosystem URLs,
not `localhost`. These live in `~/.windyfly/windyfly.toml` (or
`/opt/windyfly/windyfly.toml` on the VPS):

```toml
[ecosystem]
eternitas_url     = "https://api.eternitas.ai"
windy_pro_url     = "https://windyword.ai"
matrix_homeserver = "https://chat.windychat.ai"
windy_mail_url    = "https://mail.windymail.ai"
windy_cloud_url   = "https://cloud.windycloud.com"
```

Matching env-var overrides (populated by the systemd unit's
`EnvironmentFile`):

| Env var             | Prod value                         |
|---------------------|------------------------------------|
| `ETERNITAS_API_URL` | `https://api.eternitas.ai`             |
| `WINDY_PRO_URL`     | `https://windyword.ai`             |
| `WINDY_API_URL`     | `https://windyword.ai`             |
| `MATRIX_HOMESERVER` | `https://chat.windychat.ai`        |
| `WINDYMAIL_API_URL` | `https://mail.windymail.ai`        |
| `WINDY_CLOUD_URL`   | `https://cloud.windycloud.com`       |

**JWKS fetch:** the agent verifies Windy Pro JWTs against
`https://windyword.ai/.well-known/jwks.json`. No env var — the URL
is derived from `WINDY_PRO_URL`. Cache is 24h; rotate by bumping the
JWKS `kid` on the Pro side.

---

## 4. Bot-key rotation schedule

The `wk_` bot key minted during hatch is the credential the agent
uses to call Pro/Mail/Cloud/Chat on its own behalf (vs. under the
owner's JWT). Keys have a TTL — Pro's broker sets one, and we expose
it in `BrokeredCredential.expires_at`.

### 4.1 Automatic rotation (preferred)

The agent auto-rotates when the active `wk_` key has less than **30
days** of life remaining. The rotation runs inside the brain's daily
maintenance tick (see `windyfly.auth.bot_credentials`). No operator
action required — the new key replaces the old one atomically and
the previous one is explicitly revoked via Pro's
`POST /api/v1/agent/credentials/revoke`.

### 4.2 Manual rotation

If a key is suspected compromised (leaked to logs, pasted in a PR
diff, etc.), rotate immediately:

```bash
windy keys rotate            # rotates the active wk_ bot key
windy keys rotate --hard     # also invalidates every derived token
                             # on Mail/Cloud/Chat and re-mints them
```

Both commands are idempotent — re-running them after a successful
rotation is a no-op.

### 4.3 Observing the key age

```bash
windy passport               # shows passport + bot-key age
```

The dashboard's "Identity" panel also surfaces a warning badge at 7
days remaining.

### 4.4 Key-rotation failure mode

If Pro's broker endpoint returns 5xx during auto-rotation, the agent
keeps using the existing key and retries every 4h until either it
succeeds or the key fully expires. If the key actually expires
without a successful rotation, the agent falls back to owner-JWT
auth for service calls (visible in the audit log as `auth=owner-jwt`
rather than `auth=wk_`) and surfaces a yellow banner on the
dashboard until rotation succeeds.

---

## 5. Failure playbook

This section is written for the operator — you, at 2am, on a shaky
conference Wi-Fi, trying to figure out why the agent is silent.

### 5.1 Eternitas is down

**Symptom:** `windy passport` errors with "passport not found" or
hatch-time shows "Eternitas offline".

**Blast radius:** the agent **keeps working locally**. Eternitas is
the identity + trust layer, not the runtime. What breaks:

- `windy go` / `windy hatch` — passport issuance fails; hatch
  continues with a blank `passport_id` and saves a recovery file at
  `data/provision_recovery.json`.
- `/api/webhooks/trust` — Eternitas's `trust.changed` webhook can't
  deliver; the trust cache goes stale but never inverts (we
  fail-closed on unknown-band actions in strict mode, fail-open in
  dev).
- The Windy Mail/Cloud link-back that proves "this bot belongs to
  this passport" is queued locally and retried on the brain's daily
  tick.

**Action:**

1. Confirm with `curl https://api.eternitas.ai/health` — if 200, the
   issue is networking; if 5xx, the service is down.
2. If Eternitas will be down >6h, set `WINDYFLY_TRUST_STRICT=false`
   temporarily so clearance-gated actions don't trip. Undo once
   Eternitas recovers.
3. On recovery: `windy doctor` will replay queued link-backs
   automatically. Confirm with `windy passport` — band should be
   `good` within 5 minutes.

### 5.2 Matrix federation issue (Windy Chat unreachable)

**Symptom:** Matrix bot login fails; dashboard shows Windy Chat as
"Offline".

**Blast radius:** the agent keeps responding on CLI, SMS, and
Telegram/Slack/Discord if those are configured. Chat is just one of
many channels; its outage doesn't block the brain.

**Action:**

1. `curl https://chat.windychat.ai/_matrix/client/versions` — if
   200, the issue is bot-credential side; if 5xx, the homeserver is
   down.
2. Bot-credential fixes: re-run `windy go` to re-provision the
   Matrix bot (uses `SYNAPSE_REGISTRATION_SECRET`); or clear
   `MATRIX_BOT_TOKEN` from the env and let the agent re-login by
   password on next tick.
3. Homeserver-side outages are opaque to us — check
   https://status.windyfly.ai. The agent will reconnect
   automatically when the homeserver returns.

### 5.3 LLM quota exhausted

**Symptom:** Every response returns "Agent is processing…" or the
dashboard shows provider errors (429, 403-quota).

**Blast radius:** the agent can't answer. Everything else —
memory writes, channel adapters, trust cache — keeps ticking.

**Action (tiered):**

1. `windy budget month` — confirms we're actually over-budget vs.
   a transient provider issue. If over-budget, either raise it
   (`windy budget set <amount>`) or wait.
2. If the user has a Pro account: the `wk_` bot key includes a
   `usage_cap_tokens` field. When cap is hit, Pro's broker issues a
   **fallback provider** key (OpenAI → Gemini Free, etc.) on the
   next call. `windy passport` shows the active provider — if it
   hasn't rotated, force: `windy keys rotate`.
3. If the user is on BYOK: tell them to top-up the provider account
   and run `windy model test` to verify credit.
4. Last resort: switch to a smaller model for the remainder of the
   day — `windy model set gpt-4o-mini` (or the cheapest available
   model for the active provider). Revert overnight.

### 5.4 Everything else

`windy doctor` runs every health check we have and prints a report.
When in doubt, start there. If it's still unclear, `windy debug`
dumps the full env + config + PID table for paste-into-issue.

---

## Release checklist — the launch gates

Before cutting a production release:

- [ ] `uv run python -m pytest` — all Python tests green
- [ ] `cd gateway && bun test` — all gateway tests green
- [ ] `uv run ruff check src/` — no lint errors
- [ ] `bash scripts/smoke-test.sh` — full-path smoke against a
      running agent (see [`scripts/smoke-test.sh`](scripts/smoke-test.sh))
- [ ] `deploy/aws/FLY_DEPLOYMENT.md` bumped if infra touched
- [ ] `CHANGELOG.md` entry for the release
