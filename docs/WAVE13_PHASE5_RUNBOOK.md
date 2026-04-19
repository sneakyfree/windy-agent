# Wave 13 Phase 5 — windyfly-gateway production deploy runbook

**Target host:** `fly.windyword.ai`
**AWS account:** `819439781125` — TheWindstorm
**Phase dependencies:** Phase 1 (`api.windyword.ai`) live, JWKS `kid=37e8955762d43189`. Phase 2 (`eternitas.windyword.ai`) live.
**Cost estimate:** ~$15/mo (t3.small, 20 GB gp3, EIP — no RDS, gateway is stateless).

---

## Gated fire pattern

4 gates instead of Phase 2's 5 — no RDS because the gateway holds no persistent state. Agent runtime continues to live on user machines via `pip install windyfly && windy go`; this host only validates broker tokens, spawns the hatch-ceremony Python subprocess, and relays the SSE stream.

| Gate     | Subcommand    | What it does                                                          | Billable? | Rollback                                                           |
|----------|---------------|-----------------------------------------------------------------------|-----------|--------------------------------------------------------------------|
| **pre**  | `secrets`     | Generate `DASHBOARD_PASSWORD`; stash state file at `~/.windyfly-phase5-state` (chmod 600). | no | `rm ~/.windyfly-phase5-state`                                      |
| **FIRE 1** | `fetch-hmac`| SSH to Phase 1 (`ubuntu@100.52.10.181`) and read `BROKER_HMAC_SECRET` from `/opt/windy-pro/.env.production`. Cross-repo contract: this must match what Phase 1's credential-broker signs with. | no | n/a — read-only |
| **FIRE 2** | `ec2-eip`   | Allocate EIP, render user-data with envsubst allowlist, launch t3.small on subnet-1a, associate EIP, wait for `instance-running`. | YES (~$15/mo + $0.005/hr EIP while unassociated) | `aws ec2 terminate-instances`; `aws ec2 release-address`           |
| **FIRE 3** | `dns`       | Create or update Cloudflare A record `fly.windyword.ai` → EIP, `proxied=false` so certbot HTTP-01 can validate. Wait for DNS propagation via 1.1.1.1. | no | delete A record via Cloudflare API                                 |
| **FIRE 4** | `certbot`   | SSH in; `certbot --nginx --redirect`; then probe `/api/health` (expect 200) and `/hatch/remote` with a junk token (expect 400/401 — proves Wave 12 verify gate is active). | no | `certbot delete --cert-name fly.windyword.ai` |
| (debug)  | `status`      | Dump state file with secrets redacted. | no | n/a |

---

## What this branch adds

```
deploy/aws/phase5/
  user-data.sh               EC2 bootstrap. apt (nginx, certbot, build-essential),
                             Bun from bun.sh/install into /opt/bun, Python 3.13
                             via deadsnakes, pip install -e /opt/windyfly, writes
                             /etc/windyfly/production.env, enables systemd unit,
                             reloads nginx. Scrubs the GitHub PAT from the cloned
                             repo's remote and from /var/lib/cloud/instance/.
  windyfly-gateway.service   systemd unit. ExecStart=/usr/local/bin/bun run
                             src/server.ts. ProtectSystem=strict,
                             ReadWritePaths=/opt/windyfly/data /var/log/windyfly,
                             EnvironmentFile=/etc/windyfly/production.env.
  nginx-windyfly.conf        pre-TLS :80. `/hatch/remote` has proxy_buffering off,
                             proxy_read_timeout 300s so SSE frames flow live.
  env.production.template    source-of-truth for the production env file. Every
                             var user-data.sh substitutes is documented here.

scripts/phase5-deploy.sh     Subcommand-gated orchestrator (secrets, fetch-hmac,
                             ec2-eip, dns, certbot, status, plan). State in
                             ~/.windyfly-phase5-state.

docs/WAVE13_PHASE5_RUNBOOK.md
                             This file.
```

---

## Prereqs

1. `aws` CLI installed + configured with a `windy-ecosystem-admin` profile:
   ```
   aws sts get-caller-identity
   → account 819439781125, user windy-ecosystem-admin
   ```
2. `~/windy-prod-key.pem` (chmod 600). Also authorized for Phase 1's EC2 (`100.52.10.181`).
3. Lockbox exported:
   ```
   export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=us-east-1
   export CLOUDFLARE_DNS_TOKEN=...
   export GITHUB_CLONE_TOKEN=<PAT with repo:read>      # scrubbed from EC2 post-clone
   export SENTRY_DSN=                                   # optional; blank OK
   export WINDYFLY_BRANCH=master                        # or a deploy tag
   ```
4. Phase 1 is live with `BROKER_HMAC_SECRET` in `/opt/windy-pro/.env.production`.
5. `jq`, `envsubst`, `dig` on `$PATH`.

---

## Runbook (Grant fires gates in order)

```bash
cd ~/windy-agent

# pre
./scripts/phase5-deploy.sh secrets
./scripts/phase5-deploy.sh plan        # read what each gate will do

# FIRE 1 — cross-repo secret fetch (~2s)
./scripts/phase5-deploy.sh fetch-hmac

# FIRE 2 — EIP + EC2 launch (~2min to running, user-data completes ~90s later)
./scripts/phase5-deploy.sh ec2-eip
# tail the boot log from the instance:
ssh -i ~/windy-prod-key.pem ubuntu@$(./scripts/phase5-deploy.sh status | awk -F= '/^EIP=/{print $2}') \
  sudo tail -f /var/log/windyfly-boot.log

# FIRE 3 — DNS
./scripts/phase5-deploy.sh dns

# FIRE 4 — certbot + smoke
./scripts/phase5-deploy.sh certbot
```

---

## Pre-FIRE checklist (mirrors Phase 2's 7-pattern prechecks)

Before firing **each** gate, confirm the pattern below hasn't regressed since Phase 2 shipped:

1. **Pinned AMI** — `AMI_ID=ami-009d9173b44d0482b` matches the one Phase 2 used; no floating `latest`.
2. **GitHub PAT scrub** — `user-data.sh` rewrites the git remote to drop the token and `shred`s `/var/lib/cloud/instance/user-data.txt`. Verify by SSH'ing in post-FIRE-2 and running `cat /opt/windyfly/.git/config` (token must be absent).
3. **envsubst allowlist** — `scripts/phase5-deploy.sh::cmd_ec2_eip` lists every `${VAR}` it allows through envsubst. Any unresolved placeholder in the rendered user-data kills the gate with a loud error — *before* launching.
4. **Boot log tee'd** — `exec > >(tee /var/log/windyfly-boot.log) 2>&1` at the top of `user-data.sh`. Post-mortem debugging relies on it.
5. **State file perms** — `chmod 600 ~/.windyfly-phase5-state` on every `state_set`. `cmd_status` redacts `*SECRET*`/`*PASSWORD*`/`*TOKEN*` keys.
6. **EIP allocate → wait-running → associate** — allocate first, wait for the instance to be running, then associate. Associating before running is a silent no-op in some race windows.
7. **Cloudflare `proxied=false`** — required for certbot HTTP-01. Flip to `proxied=true` only after cert renewal is handled out-of-band (e.g. DNS-01 via API token).

---

## Rollback

```bash
# FIRE 4 — revoke cert
ssh -i ~/windy-prod-key.pem ubuntu@$EIP \
  sudo certbot delete --cert-name fly.windyword.ai

# FIRE 3 — delete A record
curl -sS -X DELETE \
  -H "Authorization: Bearer $CLOUDFLARE_DNS_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records/$CF_RECORD_ID"

# FIRE 2 — terminate EC2, release EIP
aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region us-east-1
aws ec2 wait instance-terminated --instance-ids $INSTANCE_ID --region us-east-1
aws ec2 release-address --allocation-id $EIP_ALLOC --region us-east-1

# FIRE 1 — no mutation, nothing to roll back
```

---

## Post-deploy smoke

From any machine (not the EC2):

```bash
curl -sSf https://fly.windyword.ai/api/health | jq
# → {"status":"ok", "service":"windy-fly-agent", ...}

# Junk broker_token must 400 (length cap) or 401 (bad_format) — Wave 12 gate.
curl -sS -X POST https://fly.windyword.ai/hatch/remote \
  -H 'Content-Type: application/json' \
  -d '{"broker_token":"12345678","windy_identity_id":"probe","passport_number":"probe","owner_email":"p@p","owner_phone":"+10000000000","owner_name":"p"}' \
  -o /dev/null -w '%{http_code}\n'
# → 400 (broker_token too short) or 401 (bad_format / pro_verify_endpoint_missing)

# Live end-to-end (requires a Pro-issued broker_token):
curl -N -X POST https://fly.windyword.ai/hatch/remote \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d @real-hatch-body.json
# → stream must emit the 13 canonical events in order, ending with hatch.complete.ok=true
```

The 13 canonical events (from `src/windyfly/hatch_remote.py::EVENT_ORDER`):

```
eternitas.registering   → eternitas.registered
mail.provisioning       → mail.provisioned
chat.provisioning       → chat.provisioned
cloud.provisioning      → cloud.provisioned
phone.assigning         → phone.assigned
birth_certificate.generating → birth_certificate.ready
hatch.complete
```

---

## Outstanding cross-repo dependency

Wave 12 PR #37 (merged) adds cryptographic `broker_token` verification via `POST <pro>/api/v1/agent/credentials/verify`. **Pro does not yet ship that route.** Until it does:

- `/hatch/remote` fails closed on every request with `reason=pro_verify_endpoint_missing` (correct).
- Smoke test FIRE 4 accepts both 400 and 401 as evidence the gate is live.
- Full end-to-end SSE verification blocks on a Pro PR that adds the route wrapper around `credential-broker.ts:verifyBrokerToken()`.

Track that in a follow-up — Phase 5 is deployable as "fail-closed until Pro ships the verify endpoint."
