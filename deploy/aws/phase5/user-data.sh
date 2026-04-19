#!/bin/bash
# EC2 user-data for windy-agent production (Wave 13 Phase 5).
#
# Boots an Ubuntu 24.04 t3.small into a running windyfly-gateway stack:
#   1. installs Python 3.13 (pyenv), Bun, nginx, certbot
#   2. clones the windy-agent repo at the pinned branch
#   3. pip-installs windyfly so `python -m windyfly.hatch_remote` works
#      when the gateway spawns subprocesses for the ceremony SSE stream
#   4. writes /etc/windyfly/production.env from substituted variables
#   5. boots the Bun gateway via systemd on localhost:8080
#   6. configures nginx reverse proxy for fly.windyword.ai on :80
#      (HTTPS / certbot is a separate FIRE 4 gate once DNS resolves)
#
# Substitutions happen via envsubst in scripts/phase5-deploy.sh before this
# is uploaded; see the "Allowlist substituted vars" section there.
#
# Scoping notes:
#   - Gateway is stateless — no RDS, no Redis, no volumes beyond the EBS
#     root. All agent state lives on the end-user's machine (`windy go`).
#   - libcairo2-dev is NOT needed (Phase 2 Python PDF renderer — not here).
#   - The Python venv exists only so the gateway can spawn
#     `python -m windyfly.hatch_remote` subprocesses; day-to-day agent
#     work happens on user machines.
set -euo pipefail
exec > >(tee /var/log/windyfly-boot.log) 2>&1

echo "=== windyfly-gateway user-data starting at $(date -u) ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates curl git gnupg jq openssl ufw build-essential \
  nginx certbot python3-certbot-nginx unzip

# --- Bun (Bun gateway) ------------------------------------------------------
# Install system-wide under /opt/bun so systemd can PATH it without relying
# on ubuntu's dotfiles.
install -d -o root -g root -m 0755 /opt/bun
BUN_INSTALL=/opt/bun curl -fsSL https://bun.sh/install | BUN_INSTALL=/opt/bun bash
ln -sf /opt/bun/bin/bun /usr/local/bin/bun
bun --version

# --- Python 3.13 via system apt --------------------------------------------
# 24.04 ships 3.12; we need 3.12+ which qualifies. Windyfly requires
# 3.12+ per pyproject.toml. Use the deadsnakes PPA for 3.13.
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.13 python3.13-venv python3.13-dev
python3.13 --version

# --- Clone the repo ---------------------------------------------------------
install -d -o root -g root -m 0755 /opt/windyfly
git clone --depth 1 --branch "${WINDYFLY_BRANCH}" \
  "https://${GITHUB_CLONE_TOKEN}@github.com/sneakyfree/windy-agent.git" /opt/windyfly

# CRITICAL: scrub the PAT from the git remote + /var/lib/cloud so a later
# tailer of /opt/windyfly/.git/config or /var/lib/cloud/instance/user-data.txt
# doesn't leak a token with repo-write scope.
git -C /opt/windyfly remote set-url origin \
  https://github.com/sneakyfree/windy-agent.git
shred -u /var/lib/cloud/instance/user-data.txt 2>/dev/null || \
  rm -f /var/lib/cloud/instance/user-data.txt

# --- Python venv + windyfly install ----------------------------------------
# The gateway spawns `python -m windyfly.hatch_remote`; it needs windyfly
# on the PATH of whatever Python the systemd unit invokes.
python3.13 -m venv /opt/windyfly/.venv
/opt/windyfly/.venv/bin/pip install --quiet --upgrade pip wheel setuptools
/opt/windyfly/.venv/bin/pip install --quiet -e /opt/windyfly
/opt/windyfly/.venv/bin/python -c "import windyfly; print('windyfly:', windyfly.__version__ if hasattr(windyfly, '__version__') else 'dev')"

# --- Gateway deps (Bun install) --------------------------------------------
cd /opt/windyfly/gateway
bun install --frozen-lockfile
cd -

# --- Environment file -------------------------------------------------------
install -d -o root -g root -m 0755 /etc/windyfly
umask 077
cat > /etc/windyfly/production.env <<'EOF_ENV'
WINDYFLY_ENV=production
WINDYFLY_HOME=/opt/windyfly
WINDYFLY_DB_PATH=/opt/windyfly/data/windyfly.db
LOG_LEVEL=INFO

# Ecosystem URLs (Wave 13 production hostnames)
WINDY_PRO_URL=https://api.windyword.ai
WINDY_API_URL=https://api.windyword.ai
WINDY_PRO_JWKS_URL=https://api.windyword.ai/.well-known/jwks.json
ETERNITAS_API_URL=https://eternitas.windyword.ai
ETERNITAS_URL=https://eternitas.windyword.ai
WINDYMAIL_API_URL=https://api.windymail.ai
MATRIX_HOMESERVER=https://chat.windychat.ai
WINDY_CLOUD_URL=https://api.windycloud.com

# Broker HMAC — MUST match Phase 1's BROKER_HMAC_SECRET. Retrieved from
# Phase 1's EC2 via the phase5-deploy.sh `fetch-hmac` subcommand.
BROKER_HMAC_SECRET=${BROKER_HMAC_SECRET}
WINDY_BROKER_SIGNING_SECRET=${BROKER_HMAC_SECRET}

# Dashboard auth (production fails closed without this)
DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD}

# Observability (optional)
SENTRY_DSN=${SENTRY_DSN}
SENTRY_ENV=production
EOF_ENV
chmod 0600 /etc/windyfly/production.env
chown root:root /etc/windyfly/production.env

# --- systemd unit for the gateway ------------------------------------------
cp /opt/windyfly/deploy/aws/phase5/windyfly-gateway.service \
   /etc/systemd/system/windyfly-gateway.service
systemctl daemon-reload
systemctl enable --now windyfly-gateway

# Wait briefly for gateway to come up, then verify
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/api/health > /dev/null 2>&1; then
    echo "gateway healthy after ${i}s"
    break
  fi
  sleep 1
done

# --- nginx reverse proxy ---------------------------------------------------
# HTTP-only for now; FIRE 4 (certbot) rewrites this to HTTPS.
cp /opt/windyfly/deploy/aws/phase5/nginx-windyfly.conf \
   /etc/nginx/sites-available/windyfly
ln -sf /etc/nginx/sites-available/windyfly /etc/nginx/sites-enabled/windyfly
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "=== windyfly-gateway user-data complete at $(date -u) ==="
echo "=== next: Cloudflare DNS (FIRE 3) then certbot (FIRE 4) ==="
