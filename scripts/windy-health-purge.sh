#!/usr/bin/env bash
# Purge organ-health scorecards older than N days (default 90).
# Pure delete; no tools, no DB, no network. Safe to run weekly.

set -uo pipefail

HEALTH_DIR="${WINDY_HEALTH_DIR:-/home/grantwhitmer/.windy-stress/health}"
DAYS="${WINDY_HEALTH_RETENTION_DAYS:-90}"

if [[ ! -d "$HEALTH_DIR" ]]; then
    logger -t windy-health-purge "no health dir at $HEALTH_DIR; nothing to purge"
    exit 0
fi

# Count and list before deleting so the journal has a paper trail.
BEFORE=$(find "$HEALTH_DIR" -maxdepth 1 -name "*.json" -type f | wc -l)
PURGED=$(find "$HEALTH_DIR" -maxdepth 1 -name "*.json" -type f -mtime "+$DAYS" | wc -l)

find "$HEALTH_DIR" -maxdepth 1 -name "*.json" -type f -mtime "+$DAYS" -delete

logger -t windy-health-purge \
    "purged $PURGED scorecards older than $DAYS days (was $BEFORE total in $HEALTH_DIR)"
exit 0
