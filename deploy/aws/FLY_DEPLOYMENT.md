# Windy Fly — Production Deployment

Windy Fly is a personal AI agent. It is not a service with thousands of
users — **each user runs one agent**. Deployment topology reflects that:
the default is the user's machine, and the VPS option is a hosted mirror
for users who want their agent online 24/7 without leaving a laptop
awake.

This document covers:

1. [Runtime topologies](#1-runtime-topologies)
2. [AWS EC2 option (optional VPS)](#2-aws-ec2-optional-vps)
3. [`wk_` bot-key rotation](#3-wk_-bot-key-rotation)
4. [Audit log shipping to CloudWatch](#4-audit-log-shipping-to-cloudwatch)
5. [Eternitas `trust.changed` webhook receiver scaling](#5-eternitas-trustchanged-webhook-receiver-scaling)

---

## 1. Runtime topologies

```
┌─────────────────────────────────────────────────────────────────┐
│ Topology A — user's machine (default)                           │
│                                                                 │
│   ┌─────────────┐     ┌──────────┐     ┌────────────────┐       │
│   │   `windy`   │◀───▶│  SQLite  │◀───▶│ Ecosystem APIs │       │
│   │   (Python)  │     │ (on-disk)│     │ Eternitas/Pro/ │       │
│   └─────────────┘     └──────────┘     │ Mail/Cloud/…   │       │
│          ▲                              └────────────────┘       │
│          │ launchd / systemd / user start                        │
│          ▼                                                       │
│   ┌─────────────┐                                               │
│   │   `windy`   │  CLI, channels (Slack/TG/…), dashboard at     │
│   │   gateway   │  http://localhost:7890                         │
│   └─────────────┘                                               │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Topology B — AWS EC2 (optional VPS)                             │
│                                                                 │
│   ┌─────────────┐     ┌──────────┐     ┌────────────────┐       │
│   │   EC2       │◀───▶│ EBS vol  │◀───▶│ Ecosystem APIs │       │
│   │  t4g.small  │     │ SQLite   │     └────────────────┘       │
│   └─────────────┘     └──────────┘                              │
│          ▲                                                       │
│          │ systemd (Amazon Linux 2023)                           │
│          │                                                       │
│   CloudWatch Logs ◀── audit log (bot_key_usage.jsonl)            │
│   CloudWatch Alarms ◀── systemd unit failures                    │
└─────────────────────────────────────────────────────────────────┘
```

**Default: user's machine.** The agent is provisioned during `windy go`
and runs locally. All state (memory, audit log, bot key cache, trust
cache) lives in `~/.windyfly/` or the project directory. No AWS account
required.

**Optional: hosted VPS.** For users who want the agent online while
their laptop is closed, Windy Fly can self-deploy to an EC2 instance
owned by the user. The built-in `windy cloud vps-deploy` command
provisions an AWS instance, rsyncs the agent's config + SQLite, and
starts it under `systemd`. See `src/windyfly/vps_deploy.py` for the
orchestration.

---

## 2. AWS EC2 (optional VPS)

### 2.1 Prerequisites

- AWS account with EC2 and CloudWatch Logs permissions (IAM policy
  template: `deploy/aws/iam-windyfly-runtime.json`).
- Either:
  - Windy Cloud credentials (`WINDY_CLOUD_TOKEN`) — the easy path; VPS
    provisioning rides on the hosted Cloud API.
  - Or direct AWS credentials exposed to the CLI.

### 2.2 One-command deploy

```bash
windy cloud vps-deploy \
    --region us-west-2 \
    --instance-type t4g.small \
    --dashboard-password "<something you'll remember>"
```

This:

1. Launches an EC2 instance (Amazon Linux 2023, arm64) in the user's
   chosen region.
2. Installs Python 3.12 + `windyfly` + ffmpeg.
3. Copies the local `.env`, `windyfly.db`, and `config.toml` via rsync.
4. Writes `/etc/systemd/system/windyfly.service` and enables it.
5. Opens inbound 443 (dashboard behind nginx) and 22 (SSH via SSM
   agent — no key-pair required).

### 2.3 systemd unit

```ini
# /etc/systemd/system/windyfly.service
[Unit]
Description=Windy Fly agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=windyfly
Group=windyfly
WorkingDirectory=/home/windyfly
EnvironmentFile=/home/windyfly/.env
ExecStart=/home/windyfly/.local/bin/windy daemon
Restart=on-failure
RestartSec=10
# Harden
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=/home/windyfly

[Install]
WantedBy=multi-user.target
```

### 2.4 Upgrade path

```bash
windy cloud vps-deploy --upgrade   # rsyncs new version, restarts systemd
```

The SQLite DB is never overwritten on upgrade — it lives on the EBS
volume and is snapshotted nightly via a Lambda (`deploy/aws/snap.tf`).

---

## 3. `wk_` bot-key rotation

The agent authenticates outbound ecosystem calls (Mail send, Cloud
upload, Chat message) with a `wk_`-prefixed bot key minted by the
Windy Pro account-server. **The owner's JWT is used once at mint
time; the bot key does the rest.**

### 3.1 Rotation triggers

| Trigger | Action | Owner of the decision |
|---|---|---|
| Cached key within 30 days of `expires_at` | Auto-rotate on next `get_bot_key()` call | Agent (`src/windyfly/auth/bot_credentials.py`) |
| Eternitas `trust.changed` webhook | `rotate_on_trust_change(new_band)` re-mints immediately | Agent (the new band may unlock or revoke scopes) |
| Owner-initiated rotation | `windy keys rotate` | Human |
| Compromise suspected | `windy keys revoke --reason compromised` | Human → cascades via `POST /api/v1/identity/bot-keys/revoke` |

### 3.2 Revocation cascade

```
owner                    account-server              platforms
  │                           │                          │
  │ `windy keys revoke`       │                          │
  │──────────────────────────▶│                          │
  │                           │  revoke in bot_api_keys  │
  │                           │  (SQL UPDATE status=revoked) │
  │                           │                          │
  │                           │  fan-out webhook         │
  │                           │─────────────────────────▶│  drop cached auth
  │                           │                          │
  │   summary {revoked: true} │                          │
  │◀──────────────────────────│                          │
  │                                                       │
  agent clears its local key cache (cache entry matches   │
  revoked key_id) on the same call.                       │
```

Webhook URLs to cascade to live in `config.toml`:

```toml
[auth.revoke_cascade]
webhooks = [
    "https://mail.windyword.ai/webhooks/auth",
    "https://cloud.windyword.ai/webhooks/auth",
    "https://chat.windyword.ai/webhooks/auth",
]
```

### 3.3 Operational checks

- `windy keys status` — prints current key id, scopes, age, and
  days-until-rotation.
- CloudWatch metric `WindyFly/BotKey/DaysUntilRotation` — emitted
  hourly by the agent; alarm if < 5 days.
- Audit log is the ground truth for "what did this key do" —
  see §4.

---

## 4. Audit log shipping to CloudWatch

Every `wk_`-authenticated outbound call writes one JSONL record to
`~/.windyfly/data/audit/bot_key_usage.jsonl`:

```json
{"timestamp":"2026-04-16T20:11:03.118+00:00","key_id":"wbk_01HXXX","scope_used":"cloud:upload","target_url":"https://cloud.windyword.ai/api/v1/archive/agent","response_status":201,"latency_ms":184.3}
```

### 4.1 Shipping from the user's machine (opt-in)

If the user has opted in to centralized observability:

```bash
windy observe enable --to cloudwatch --log-group /windyfly/audit
```

This drops a small launchd/systemd unit that tails
`bot_key_usage.jsonl` and pipes each line through the CloudWatch
Logs agent. Credentials come from the user's own AWS profile — the
log group lives in **the user's account, not ours**.

### 4.2 Shipping from EC2 VPS (default)

The `windy cloud vps-deploy` command installs the CloudWatch agent and
pre-configures it:

```yaml
# /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.yaml
logs:
  logs_collected:
    files:
      collect_list:
        - file_path: /home/windyfly/data/audit/bot_key_usage.jsonl
          log_group_name: /windyfly/audit/${INSTANCE_ID}
          log_stream_name: bot_key_usage
          timestamp_format: "%Y-%m-%dT%H:%M:%S.%f%z"
        - file_path: /var/log/syslog
          log_group_name: /windyfly/system/${INSTANCE_ID}
          log_stream_name: syslog
```

### 4.3 Rotation

The audit log is append-only JSONL. Rotate with `logrotate` at 100 MB:

```
# /etc/logrotate.d/windyfly
/home/windyfly/data/audit/bot_key_usage.jsonl {
    size 100M
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
```

CloudWatch retention is set to 90 days by the Terraform in
`deploy/aws/cloudwatch.tf`.

### 4.4 Useful Insights queries

```
# Denied actions in the last hour
fields @timestamp, key_id, scope_used, target_url, response_status
| filter response_status >= 400
| stats count() by response_status, scope_used
```

```
# p50/p99 latency by scope
fields @timestamp, scope_used, latency_ms
| stats percentile(latency_ms, 50), percentile(latency_ms, 99) by scope_used
```

---

## 5. Eternitas `trust.changed` webhook receiver scaling

The agent subscribes to `trust.changed` from Eternitas so cached trust
snapshots flush immediately on band/clearance flips (rather than
waiting for the 5-minute TTL). One webhook per band/clearance change
per agent — **traffic is tiny** (dozens/day per agent, at most).

### 5.1 On the user's machine

The agent receives webhooks on its local gateway at
`POST http://localhost:7890/webhooks/trust`. When the machine is off
the network, Eternitas retries with exponential backoff per
`docs/webhooks.md` — when the agent comes back online the backlog
flushes. No shared infrastructure required.

### 5.2 On a VPS

Eternitas POSTs directly to the EC2 instance's public endpoint. Scale
is single-instance: even the heaviest user sees <10 webhooks/minute
under normal operation; trust-change events are rare by design.

If you need cross-instance scaling (e.g., an enterprise that runs many
agents behind one domain):

```
Eternitas ──▶ CloudFront ──▶ API Gateway ──▶ SQS ──▶ Lambda
                                               │
                                               └──▶ fan-out to agent EC2s
                                                    via per-instance SQS
```

But that's **not the default**. For the personal-agent case, the agent
receives its own webhooks directly. This keeps the blast radius of a
single bad webhook to one user and avoids the "our agent platform is
down → all agents blind to trust changes" failure mode.

### 5.3 Signature verification

Eternitas signs webhooks with **both** `X-Eternitas-Signature` (HMAC)
and `X-Windy-Signature` (detached ES256 JWS). The JWS verifies against
the public JWKS at `/.well-known/eternitas-keys` — cached locally.

Status: JWS verification is **not yet enforced** as of Wave 5 — the
next hardening pass (see `src/windyfly/trust/webhook.py`). Until then,
the webhook receiver should only be exposed on loopback or behind a
VPC-restricted endpoint.

### 5.4 Retry semantics

Eternitas retries failed deliveries 6 times with exponential backoff
up to 1 hour. Receivers must be **idempotent** — the webhook handler
(`handle_trust_changed`) currently invalidates the cache by passport,
so double-delivery is a no-op. Rotation on re-delivery just re-mints
an identical key, which is also safe.

---

## 6. Checklists

### New deployment
- [ ] Install agent on user's machine (`windy go`)
- [ ] Point `ETERNITAS_URL` at production Eternitas
- [ ] Set `WINDYFLY_TRUST_STRICT=1`
- [ ] Mint an initial `wk_` bot key (`windy keys mint`)
- [ ] Subscribe to `trust.changed` for this passport
- [ ] (Optional) `windy cloud vps-deploy` for 24/7
- [ ] (Optional) `windy observe enable` for CloudWatch shipping

### Incident response — suspected key compromise
1. `windy keys revoke --reason compromised`
2. Verify cascade in account-server audit log
3. `windy keys mint --rotate` to re-mint
4. Grep audit log for suspicious `response_status` / `target_url` to
   assess blast radius: `jq 'select(.timestamp > "<when>") | select(.response_status >= 400 or .scope_used == "cloud:upload")' bot_key_usage.jsonl`

### Incident response — trust band drop
1. `trust.changed` webhook fires automatically → local cache flushes,
   key rotates, owner gets email
2. Owner opens trust dashboard, sees which actions just locked
3. No manual action required on the agent side
