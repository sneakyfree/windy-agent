#!/bin/bash
# Wave 13 Phase 5 — windyfly-gateway production deploy orchestrator.
#
# Stateless gateway — 4 FIRE gates instead of Phase 2's 5 (no RDS).
# Run each gate on Grant's "FIRE <n>" signal. State persists in
# ~/.windyfly-phase5-state (chmod 600) between invocations.
#
#   ./scripts/phase5-deploy.sh plan         # dry — print every cmd
#   ./scripts/phase5-deploy.sh secrets      # generate + stash DASHBOARD_PASSWORD
#   ./scripts/phase5-deploy.sh fetch-hmac   # FIRE 1 — pull BROKER_HMAC_SECRET
#                                           #           from Phase 1's EC2
#   ./scripts/phase5-deploy.sh ec2-eip      # FIRE 2 — EIP alloc + EC2 launch
#   ./scripts/phase5-deploy.sh dns          # FIRE 3 — Cloudflare A record
#   ./scripts/phase5-deploy.sh certbot      # FIRE 4 — TLS via Let's Encrypt
#   ./scripts/phase5-deploy.sh status       # dump state (secrets redacted)
#
# Required env vars (source from ~/kit-army-config/ACCESS_LOCKBOX.md §10):
#   AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY
#   CLOUDFLARE_DNS_TOKEN
# Optional:
#   GITHUB_CLONE_TOKEN    — PAT with repo read (scrubbed from EC2 after clone)
#   SENTRY_DSN            — blank disables Sentry
#   WINDYFLY_BRANCH       — default main
#
set -euo pipefail

STATE_FILE="${WINDYFLY_PHASE5_STATE:-$HOME/.windyfly-phase5-state}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ─── Shared infra constants (match wave13-deploy-prompts.md) ────────────────
VPC=vpc-011cc35a43403f9ef
SUBNET_PUBLIC_1A=subnet-03fcb275dd93b93a4        # 1a — spread AZs from Phase 2
SG_WEB=sg-05024168bf3105182
SG_ADMIN=sg-0f70b0451e92558a2
KEY_NAME=windy-prod-key
KEY_PATH="$HOME/windy-prod-key.pem"
EC2_NAME=windyfly-gateway
AMI_ID=ami-009d9173b44d0482b                     # pinned — Ubuntu 24.04
CF_ZONE_NAME=windyword.ai
HOSTNAME=fly.windyword.ai
ADMIN_EMAIL=grantwhitmer3@gmail.com
PHASE1_EC2_IP="${PHASE1_EC2_IP:-100.52.10.181}"  # ubuntu@100.52.10.181 per Grant

# ─── Helpers ────────────────────────────────────────────────────────────────
say() { printf "\n\033[1;36m%s\033[0m\n" "$*"; }
die() { printf "\033[1;31mERROR:\033[0m %s\n" "$*" >&2; exit 1; }

require_tools() {
  for t in aws jq envsubst curl openssl ssh dig; do
    command -v "$t" >/dev/null || die "tool '$t' not installed — see WAVE13_PHASE5_RUNBOOK.md prereqs"
  done
}
require_env() {
  for var in "$@"; do
    [[ -n "${!var:-}" ]] || die "env var $var is empty; source lockbox first"
  done
}
require_aws() {
  command -v aws >/dev/null || die "aws CLI not installed"
  aws sts get-caller-identity --region us-east-1 --query Arn --output text >/dev/null 2>&1 \
    || die "aws sts get-caller-identity failed — check ~/.aws/credentials"
}

gen_hex()     { openssl rand -hex "$1"; }
gen_urlsafe() { openssl rand -base64 "$1" | tr -d '/+=' | head -c "$1"; }

state_set() {
  local key="$1" val="$2"
  touch "$STATE_FILE"; chmod 600 "$STATE_FILE"
  if grep -q "^${key}=" "$STATE_FILE" 2>/dev/null; then
    # Portable in-place edit (macOS sed quirk).
    sed -i.bak "s|^${key}=.*$|${key}=${val}|" "$STATE_FILE" && rm -f "${STATE_FILE}.bak"
  else
    printf '%s=%s\n' "$key" "$val" >> "$STATE_FILE"
  fi
}
state_get() { grep "^${1}=" "$STATE_FILE" 2>/dev/null | head -1 | cut -d= -f2-; }
state_load() {
  [[ -f "$STATE_FILE" ]] || die "state file $STATE_FILE missing — run 'secrets' first"
  # shellcheck disable=SC1090
  set -a; . "$STATE_FILE"; set +a
}

redact() {
  # Mask secrets when dumping state. Keeps the first 4 + last 4 chars.
  local v="$1"
  (( ${#v} <= 10 )) && { printf '***'; return; }
  printf '%s…%s' "${v:0:4}" "${v: -4}"
}

# ─── Subcommands ────────────────────────────────────────────────────────────

cmd_secrets() {
  say "Generating DASHBOARD_PASSWORD, stashing in $STATE_FILE (chmod 600)"
  state_set DASHBOARD_PASSWORD "$(gen_urlsafe 32)"
  : "${SENTRY_DSN:=}"
  state_set SENTRY_DSN "${SENTRY_DSN}"
  say "Next gate: FIRE 1 (fetch-hmac) — run ./scripts/phase5-deploy.sh fetch-hmac"
}

cmd_fetch_hmac() {
  require_tools
  [[ -f "$KEY_PATH" ]] || die "ssh key $KEY_PATH not found"
  chmod 600 "$KEY_PATH" 2>/dev/null || true

  say "FIRE 1 — SSH to Phase 1 EC2 ($PHASE1_EC2_IP) and grep BROKER_HMAC_SECRET"

  # Only reads a single variable. The ssh command itself doesn't echo
  # the secret (we suppress -v), and we never persist it to a file with
  # >600 perms.
  local secret
  secret=$(ssh -o StrictHostKeyChecking=accept-new \
               -o UserKnownHostsFile=/dev/null \
               -o BatchMode=yes \
               -i "$KEY_PATH" "ubuntu@${PHASE1_EC2_IP}" \
               "sudo grep -E '^BROKER_HMAC_SECRET=' /opt/windy-pro/.env.production | cut -d= -f2- | tr -d '\"'" \
               2>/dev/null | tr -d '\r\n')

  [[ -n "$secret" ]] || die "could not read BROKER_HMAC_SECRET from Phase 1 EC2"
  [[ "${#secret}" -ge 32 ]] || die "BROKER_HMAC_SECRET looks too short (${#secret} chars)"

  state_set BROKER_HMAC_SECRET "$secret"
  say "    BROKER_HMAC_SECRET stashed (length ${#secret}, preview $(redact "$secret"))"
  say "Next gate: FIRE 2 (ec2-eip) — run ./scripts/phase5-deploy.sh ec2-eip"
}

cmd_ec2_eip() {
  require_tools; require_aws
  require_env AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  state_load
  require_env DASHBOARD_PASSWORD BROKER_HMAC_SECRET
  : "${WINDYFLY_BRANCH:=main}"
  : "${GITHUB_CLONE_TOKEN:?set GITHUB_CLONE_TOKEN to a GitHub PAT with repo:read (scrubbed from EC2 after clone)}"
  : "${SENTRY_DSN:=}"

  say "FIRE 2 — allocate Elastic IP + launch EC2 t3.small"

  # 2a: EIP
  EIP_JSON=$(aws ec2 allocate-address --domain vpc --region us-east-1 \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Project,Value=Windy},{Key=Product,Value=windyfly},{Key=Purpose,Value=hatch-gateway}]")
  EIP=$(echo "$EIP_JSON" | jq -r .PublicIp)
  EIP_ALLOC=$(echo "$EIP_JSON" | jq -r .AllocationId)
  state_set EIP "$EIP"
  state_set EIP_ALLOC "$EIP_ALLOC"
  say "    EIP: $EIP  (alloc $EIP_ALLOC)"

  # 2b: render user-data via envsubst with an explicit allowlist — any
  # unresolved ${VAR} after render is a missing env var (7-bug-pattern #3).
  export BROKER_HMAC_SECRET DASHBOARD_PASSWORD SENTRY_DSN
  export WINDYFLY_BRANCH GITHUB_CLONE_TOKEN

  UD_SRC="$REPO_ROOT/deploy/aws/phase5/user-data.sh"
  UD_TMP="$(mktemp /tmp/windyfly-user-data.XXXXXX.sh)"
  envsubst '${BROKER_HMAC_SECRET}
${DASHBOARD_PASSWORD}
${SENTRY_DSN}
${WINDYFLY_BRANCH}
${GITHUB_CLONE_TOKEN}' < "$UD_SRC" > "$UD_TMP"

  if grep -Eq '\$\{[A-Z0-9_]+\}' "$UD_TMP"; then
    echo "unresolved placeholders in rendered user-data:" >&2
    grep -nE '\$\{[A-Z0-9_]+\}' "$UD_TMP" >&2
    rm -f "$UD_TMP"
    die "fix missing env vars and re-run"
  fi

  # 2c: EC2 launch
  INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type t3.small \
    --key-name "$KEY_NAME" \
    --subnet-id "$SUBNET_PUBLIC_1A" \
    --security-group-ids "$SG_WEB" "$SG_ADMIN" \
    --associate-public-ip-address \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3,DeleteOnTermination=true}' \
    --user-data "file://$UD_TMP" \
    --region us-east-1 \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$EC2_NAME},{Key=Project,Value=Windy},{Key=Product,Value=windyfly},{Key=Purpose,Value=hatch-gateway},{Key=Environment,Value=production}]" \
    --query 'Instances[0].InstanceId' --output text)
  state_set INSTANCE_ID "$INSTANCE_ID"
  say "    Instance: $INSTANCE_ID — waiting for running state"

  aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region us-east-1

  # 2d: associate EIP
  aws ec2 associate-address \
    --instance-id "$INSTANCE_ID" \
    --allocation-id "$EIP_ALLOC" \
    --region us-east-1 > /dev/null
  say "    EIP $EIP associated → $INSTANCE_ID"

  rm -f "$UD_TMP"
  say "user-data is running on the box (tee'd to /var/log/windyfly-boot.log);"
  say "Bun + Python install + nginx take ~90s on t3.small."
  say "Next gate: FIRE 3 (dns) — run ./scripts/phase5-deploy.sh dns"
}

cmd_dns() {
  require_tools; require_env CLOUDFLARE_DNS_TOKEN
  state_load; require_env EIP

  say "FIRE 3 — Cloudflare A record $HOSTNAME → $EIP (proxied=false for certbot)"

  ZONE_ID=$(curl -sS -H "Authorization: Bearer $CLOUDFLARE_DNS_TOKEN" \
    "https://api.cloudflare.com/client/v4/zones?name=$CF_ZONE_NAME" \
    | jq -r '.result[0].id // ""')
  [[ -n "$ZONE_ID" ]] || die "Cloudflare zone $CF_ZONE_NAME not found"
  state_set CF_ZONE_ID "$ZONE_ID"

  EXISTING=$(curl -sS -H "Authorization: Bearer $CLOUDFLARE_DNS_TOKEN" \
    "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=A&name=$HOSTNAME" \
    | jq -r '.result[0].id // ""')

  PAYLOAD=$(jq -nc --arg ip "$EIP" --arg name "$HOSTNAME" \
    '{type:"A", name:$name, content:$ip, proxied:false, ttl:300}')

  if [[ -n "$EXISTING" ]]; then
    RESP=$(curl -sS -X PUT \
      -H "Authorization: Bearer $CLOUDFLARE_DNS_TOKEN" \
      -H "Content-Type: application/json" \
      --data "$PAYLOAD" \
      "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$EXISTING")
    RECORD_ID="$EXISTING"
  else
    RESP=$(curl -sS -X POST \
      -H "Authorization: Bearer $CLOUDFLARE_DNS_TOKEN" \
      -H "Content-Type: application/json" \
      --data "$PAYLOAD" \
      "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records")
    RECORD_ID=$(echo "$RESP" | jq -r '.result.id // ""')
  fi
  [[ "$(echo "$RESP" | jq -r '.success')" == "true" ]] \
    || die "Cloudflare API error: $RESP"
  state_set CF_RECORD_ID "$RECORD_ID"

  say "    Record id: $RECORD_ID — verifying propagation..."
  for i in $(seq 1 30); do
    got=$(dig +short "$HOSTNAME" @1.1.1.1 | tail -1)
    if [[ "$got" == "$EIP" ]]; then
      say "    DNS resolves to $EIP after ${i} attempt(s)"
      break
    fi
    sleep 2
  done

  say "Next gate: FIRE 4 (certbot) — run ./scripts/phase5-deploy.sh certbot"
}

cmd_certbot() {
  state_load
  require_env EIP
  [[ -f "$KEY_PATH" ]] || die "ssh key $KEY_PATH not found"
  chmod 600 "$KEY_PATH" 2>/dev/null || true

  say "FIRE 4 — SSH to $EIP and run certbot for $HOSTNAME"

  ssh -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile=/dev/null \
      -i "$KEY_PATH" "ubuntu@$EIP" \
      "sudo certbot --nginx -d $HOSTNAME \
        --email $ADMIN_EMAIL --agree-tos --non-interactive --redirect"

  say "    verifying TLS..."
  curl -sSf --max-time 10 "https://$HOSTNAME/api/health" \
    || die "HTTPS health check failed"

  say "    /api/health OK. Running /hatch/remote smoke..."
  # 401 with reason=broker_secret_not_configured is acceptable during
  # the first minutes; a successful SSE stream requires Phase 1 to be
  # reachable + a real broker_token in hand.
  RESP=$(curl -sS -o /dev/null -w '%{http_code}' \
    -X POST "https://$HOSTNAME/hatch/remote" \
    -H 'Content-Type: application/json' \
    --data '{"broker_token":"12345678","windy_identity_id":"probe","passport_number":"probe","owner_email":"probe@probe","owner_phone":"+10000000000","owner_name":"probe"}')
  if [[ "$RESP" == "400" || "$RESP" == "401" ]]; then
    say "    /hatch/remote bad-token probe returned $RESP — gate is active"
  else
    die "/hatch/remote returned $RESP on a junk token — verify gate not wired"
  fi

  say "Phase 5 gateway live at https://$HOSTNAME"
}

cmd_status() {
  [[ -f "$STATE_FILE" ]] || { say "(no state file)"; return; }
  say "=== Phase 5 state (secrets redacted) ==="
  while IFS='=' read -r k v; do
    case "$k" in
      ""|'#'*) continue ;;
      *SECRET*|*PASSWORD*|*TOKEN*) printf '%s=%s\n' "$k" "$(redact "$v")" ;;
      *) printf '%s=%s\n' "$k" "$v" ;;
    esac
  done < "$STATE_FILE"
}

cmd_plan() {
  cat <<PLAN
Wave 13 Phase 5 deploy plan (FIRE-gated subcommands):

  secrets       Generate DASHBOARD_PASSWORD; stash in $STATE_FILE (chmod 600).

  fetch-hmac    [FIRE 1] ssh ubuntu@${PHASE1_EC2_IP}
                         sudo grep BROKER_HMAC_SECRET= /opt/windy-pro/.env.production

  ec2-eip       [FIRE 2] aws ec2 allocate-address --domain vpc
                         aws ec2 run-instances
                             --image-id $AMI_ID (pinned Ubuntu 24.04)
                             --type t3.small --subnet $SUBNET_PUBLIC_1A
                             --sg $SG_WEB,$SG_ADMIN --key $KEY_NAME
                             --user-data file://<rendered>
                         aws ec2 associate-address

  dns           [FIRE 3] POST Cloudflare API:
                         zone=$CF_ZONE_NAME  record=$HOSTNAME
                         type=A  content=<EIP>  proxied=false  ttl=300

  certbot       [FIRE 4] ssh ubuntu@<EIP>
                         sudo certbot --nginx -d $HOSTNAME
                         + verify /api/health 200
                         + verify /hatch/remote 401 on junk token

  status        Dump state file (secrets redacted)
PLAN
}

# ─── Dispatch ───────────────────────────────────────────────────────────────
case "${1:-}" in
  secrets)    cmd_secrets     ;;
  fetch-hmac) cmd_fetch_hmac  ;;
  ec2-eip)    cmd_ec2_eip     ;;
  dns)        cmd_dns         ;;
  certbot)    cmd_certbot     ;;
  status)     cmd_status      ;;
  plan|"")    cmd_plan        ;;
  *) die "unknown subcommand: $1 — try 'plan'" ;;
esac
