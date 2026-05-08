#!/usr/bin/env bash
# Phase A M1 spike — Lambda deploy script.
#
# Usage:
#   ./deploy.sh                    # full deploy (provision + package + upload)
#   ./deploy.sh package-only       # just zip; don't upload
#   ./deploy.sh smoke              # invoke deployed Lambda with test event
#
# Requires (read from env):
#   AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (lockbox §AWS-TheWindstorm windy-ecosystem-admin)
#   ANTHROPIC_API_KEY (lockbox §Anthropic — set in Lambda env, NOT in this script)
#
# Region: us-east-1 (per ADR-009 + Phase A region decision).

set -euo pipefail

ACTION="${1:-full}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
BUCKET="windyfly-cloud-runtime-state-dev"
FUNCTION_NAME="windyfly-runtime-spike-dev"
ROLE_NAME="windyfly-runtime-spike-role"
ROLE_ARN_FILE=".role_arn"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR"

# ─── Helpers ─────────────────────────────────────────────────────────

log() { printf '%s [phase-a-m1] %s\n' "$(date +'%H:%M:%S')" "$*"; }

require_aws_auth() {
    if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
        log "ERROR: AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY required (lockbox §AWS-TheWindstorm)"
        exit 2
    fi
    aws sts get-caller-identity >/dev/null
}

# ─── Step 1: ensure S3 bucket exists ────────────────────────────────

provision_s3() {
    log "ensuring S3 bucket: $BUCKET"
    if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
        log "  ✓ already exists"
    else
        aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
        aws s3api put-bucket-versioning --bucket "$BUCKET" \
            --versioning-configuration Status=Enabled
        aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
            --lifecycle-configuration '{"Rules":[{"ID":"expire-noncurrent","Status":"Enabled","Filter":{"Prefix":""},"NoncurrentVersionExpiration":{"NoncurrentDays":30}}]}'
        log "  ✓ created with versioning + 30-day non-current expiration"
    fi
}

# ─── Step 2: ensure IAM role exists ──────────────────────────────────

provision_iam_role() {
    log "ensuring IAM role: $ROLE_NAME"
    local role_arn
    if role_arn=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text 2>/dev/null); then
        log "  ✓ already exists ($role_arn)"
        echo "$role_arn" > "$ROLE_ARN_FILE"
        return
    fi

    log "  creating role with Lambda trust policy..."
    role_arn=$(aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{
            "Version":"2012-10-17",
            "Statement":[{
                "Effect":"Allow",
                "Principal":{"Service":"lambda.amazonaws.com"},
                "Action":"sts:AssumeRole"
            }]
        }' \
        --query 'Role.Arn' --output text)
    echo "$role_arn" > "$ROLE_ARN_FILE"

    log "  attaching CloudWatch logs policy..."
    aws iam attach-role-policy --role-name "$ROLE_NAME" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    log "  attaching inline S3 policy (read+write on $BUCKET)..."
    aws iam put-role-policy --role-name "$ROLE_NAME" \
        --policy-name S3StateAccess \
        --policy-document "$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": [
            "s3:GetObject",
            "s3:PutObject",
            "s3:DeleteObject"
        ],
        "Resource": "arn:aws:s3:::$BUCKET/*"
    }, {
        "Effect": "Allow",
        "Action": ["s3:ListBucket"],
        "Resource": "arn:aws:s3:::$BUCKET"
    }]
}
EOF
)"

    log "  ✓ role created — IAM propagation takes ~10s"
    sleep 12
}

# ─── Step 3: package Lambda zip ──────────────────────────────────────

package_lambda() {
    log "packaging Lambda zip..."
    rm -f deployment.zip
    rm -rf build/

    mkdir -p build/
    # Copy handler
    cp lambda_handler.py build/

    # Install boto3 — actually, Lambda Python runtime ships boto3 pre-installed,
    # so we don't need to bundle it. Keeps the zip slim.
    # Same for json, os, sqlite3, urllib, time — all stdlib.

    cd build/
    zip -q -r ../deployment.zip .
    cd ..

    log "  ✓ deployment.zip created ($(du -h deployment.zip | cut -f1))"
}

# ─── Step 4: deploy / update Lambda function ─────────────────────────

deploy_lambda() {
    log "deploying Lambda: $FUNCTION_NAME"
    local role_arn
    role_arn=$(cat "$ROLE_ARN_FILE")

    if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
        log "  function exists — updating code..."
        aws lambda update-function-code \
            --function-name "$FUNCTION_NAME" \
            --zip-file fileb://deployment.zip \
            --region "$REGION" >/dev/null
        sleep 3  # update propagation
        log "  updating configuration..."
        aws lambda update-function-configuration \
            --function-name "$FUNCTION_NAME" \
            --environment "Variables={AGENT_STATE_BUCKET=$BUCKET}" \
            --memory-size 512 \
            --timeout 30 \
            --region "$REGION" >/dev/null
    else
        log "  creating new function..."
        aws lambda create-function \
            --function-name "$FUNCTION_NAME" \
            --runtime python3.12 \
            --role "$role_arn" \
            --handler lambda_handler.lambda_handler \
            --zip-file fileb://deployment.zip \
            --memory-size 512 \
            --timeout 30 \
            --environment "Variables={AGENT_STATE_BUCKET=$BUCKET}" \
            --region "$REGION" >/dev/null
    fi

    log "  ✓ deployed — manually set ANTHROPIC_API_KEY via console or CLI"
    log "  CLI:    aws lambda update-function-configuration --function-name $FUNCTION_NAME --environment Variables={AGENT_STATE_BUCKET=$BUCKET,ANTHROPIC_API_KEY=sk-ant-...}"
}

# ─── Step 5: smoke test ──────────────────────────────────────────────

smoke() {
    log "smoke test: invoking $FUNCTION_NAME"
    local payload
    payload=$(cat <<'EOF'
{
    "message": "Hello agent — this is the Phase A M1 spike smoke test. Tell me you received this.",
    "user_id": "smoke-test-user-001"
}
EOF
)
    aws lambda invoke \
        --function-name "$FUNCTION_NAME" \
        --payload "$(echo "$payload" | base64)" \
        --cli-binary-format raw-in-base64-out \
        --region "$REGION" \
        /tmp/lambda-response.json

    log "response:"
    cat /tmp/lambda-response.json
    echo
}

# ─── Main ────────────────────────────────────────────────────────────

case "$ACTION" in
    full)
        require_aws_auth
        provision_s3
        provision_iam_role
        package_lambda
        deploy_lambda
        log ""
        log "✅ Day-1 deploy complete. Set ANTHROPIC_API_KEY then run: ./deploy.sh smoke"
        ;;
    package-only)
        package_lambda
        ;;
    smoke)
        require_aws_auth
        smoke
        ;;
    *)
        echo "usage: $0 [full|package-only|smoke]" >&2
        exit 2
        ;;
esac
