#!/usr/bin/env bash
# lint-canonical-domains.sh
# ---------------------------------------------------------------
# Scans a directory tree for any banned domain string declared in
# canonical-domains.json (the Windy ecosystem source of truth).
#
# Usage:
#   lint-canonical-domains.sh [path]
#   lint-canonical-domains.sh --config /path/to/canonical-domains.json [path]
#
# Defaults:
#   path    = .
#   config  = <script-dir>/../canonical-domains.json
#
# Exit codes:
#   0  clean
#   1  one or more banned domains found
#   2  configuration / usage error
#
# Pure shell. Uses ripgrep (rg) when available, falls back to grep.
# ---------------------------------------------------------------

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_CONFIG="${SCRIPT_DIR}/../canonical-domains.json"

CONFIG_PATH=""
TARGET_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --config=*)
      CONFIG_PATH="${1#--config=}"
      shift
      ;;
    -h|--help)
      grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      if [[ -z "$TARGET_PATH" ]]; then
        TARGET_PATH="$1"
      else
        echo "lint-canonical-domains: unexpected argument: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

TARGET_PATH="${TARGET_PATH:-.}"
CONFIG_PATH="${CONFIG_PATH:-$DEFAULT_CONFIG}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "lint-canonical-domains: config not found: $CONFIG_PATH" >&2
  exit 2
fi

if [[ ! -d "$TARGET_PATH" && ! -f "$TARGET_PATH" ]]; then
  echo "lint-canonical-domains: target path does not exist: $TARGET_PATH" >&2
  exit 2
fi

# ---------------------------------------------------------------
# Extract the banned[] list from canonical-domains.json.
# Pure shell: pull lines between `"banned": [` and the matching `]`,
# then peel out each quoted string. Handles trailing commas.
# ---------------------------------------------------------------
BANNED_RAW="$(
  awk '
    /"banned"[[:space:]]*:[[:space:]]*\[/ { inblk=1; next }
    inblk && /^[[:space:]]*\]/           { inblk=0; exit }
    inblk { print }
  ' "$CONFIG_PATH"
)"

BANNED_LIST="$(
  printf '%s\n' "$BANNED_RAW" \
    | grep -oE '"[^"]+"' \
    | sed 's/^"//; s/"$//'
)"

if [[ -z "$BANNED_LIST" ]]; then
  echo "lint-canonical-domains: no banned[] entries parsed from $CONFIG_PATH" >&2
  exit 2
fi

# Build a single alternation regex. Escape regex metachars (we have dots).
ALT_PATTERN="$(
  printf '%s\n' "$BANNED_LIST" \
    | sed 's/[][\\.^$*+?(){}|]/\\&/g' \
    | paste -sd'|' -
)"

# ---------------------------------------------------------------
# Exclusion lists. Globs (for rg) and grep --exclude-dir / --exclude.
# ---------------------------------------------------------------
EXCLUDE_DIRS=(
  ".git"
  "node_modules"
  "dist"
  "dist-pre-bundling"
  "build"
  ".build"
  "out"
  "out-vscode"
  "coverage"
  ".venv"
  "venv"
  "__pycache__"
  ".next"
  ".turbo"
  ".cache"
  "wave11-evidence"
)

# Path-glob exclusions (matched against the full path, not just the basename).
# Use these for "built output" directories that live alongside source.
EXCLUDE_PATHS=(
  "**/public/assets/**"
  "**/public/build/**"
  "**/static/assets/**"
  "**/gateway/public/**"
  "**/docs/audit/**"
  "**/docs/hardening/artifacts/**"
  "**/wave11-evidence/**"
)

# File patterns to skip. Dated audit reports look like FOO_2026-04-21.md or
# similar (4-digit year, dashes). We catch them via a regex below.
EXCLUDE_FILES=(
  "*.bak"
  "*.lock"
  "package-lock.json"
  "yarn.lock"
  "pnpm-lock.yaml"
  "Cargo.lock"
  "poetry.lock"
  "uv.lock"
  "CHANGELOG*"
  "*REPORT*.md"
  "*MORNING_BRIEFING*.md"
  "*MORNING-BRIEFING*.md"
  "*MERGE_TRIAGE*"
  "*-MERGE-TRIAGE*"
  "*_AUDIT_*.md"
  "*-AUDIT-*.md"
  "*_2026-*.md"
  "*-2026-*.md"
  "*_RUNBOOK.md"
  "*-RUNBOOK.md"
  "*_RUNBOOK_*.md"
  "*_DEPLOY_PLAN.md"
  "*-DEPLOY-PLAN.md"
  "WHITE_GLOVE_*.md"
  "GAP_ANALYSIS*.md"
  "STATIC_AUDIT*.md"
  "canonical-domains.json"
  "lint-canonical-domains.sh"
  "canonical-domains-lint-integration.md"
  "ACCESS_LOCKBOX.md"
  "ACCESS_LOCKBOX.md.bak"
  "*.db"
  "*.sqlite"
  "*.sqlite3"
  "*.min.js"
  "*.min.css"
  "*.map"
  "*.tsbuildinfo"
)

USE_RG=0
if command -v rg >/dev/null 2>&1; then
  USE_RG=1
fi

TMP_HITS="$(mktemp -t canonlint.XXXXXX)"
trap 'rm -f "$TMP_HITS"' EXIT

if [[ $USE_RG -eq 1 ]]; then
  RG_ARGS=(--no-heading --line-number --color=never --hidden)
  for d in "${EXCLUDE_DIRS[@]}"; do
    RG_ARGS+=(--glob "!${d}" --glob "!**/${d}/**")
  done
  for f in "${EXCLUDE_FILES[@]}"; do
    RG_ARGS+=(--glob "!${f}")
  done
  for p in "${EXCLUDE_PATHS[@]}"; do
    RG_ARGS+=(--glob "!${p}")
  done
  rg "${RG_ARGS[@]}" -e "$ALT_PATTERN" "$TARGET_PATH" > "$TMP_HITS" 2>/dev/null || true
else
  GREP_ARGS=(-rnE -I)
  for d in "${EXCLUDE_DIRS[@]}"; do
    GREP_ARGS+=("--exclude-dir=${d}")
  done
  for f in "${EXCLUDE_FILES[@]}"; do
    GREP_ARGS+=("--exclude=${f}")
  done
  # grep has no native path-glob support; post-filter via grep -v.
  GREP_PATH_FILTER=""
  for p in "${EXCLUDE_PATHS[@]}"; do
    # Convert ** -> .*, * -> [^/]*, escape dots.
    re="$(printf '%s' "$p" | sed 's/\./\\./g; s/\*\*/__DBLSTAR__/g; s/\*/[^\/]*/g; s/__DBLSTAR__/.*/g')"
    if [[ -z "$GREP_PATH_FILTER" ]]; then
      GREP_PATH_FILTER="$re"
    else
      GREP_PATH_FILTER="$GREP_PATH_FILTER|$re"
    fi
  done
  if [[ -n "$GREP_PATH_FILTER" ]]; then
    grep "${GREP_ARGS[@]}" "$ALT_PATTERN" "$TARGET_PATH" 2>/dev/null \
      | grep -vE "^($GREP_PATH_FILTER):" > "$TMP_HITS" || true
  else
    grep "${GREP_ARGS[@]}" "$ALT_PATTERN" "$TARGET_PATH" > "$TMP_HITS" 2>/dev/null || true
  fi
fi

if [[ ! -s "$TMP_HITS" ]]; then
  echo "lint-canonical-domains: OK ($(printf '%s\n' "$BANNED_LIST" | wc -l | tr -d ' ') banned patterns checked, 0 violations) — $TARGET_PATH"
  exit 0
fi

VIOLATION_COUNT="$(wc -l < "$TMP_HITS" | tr -d ' ')"
echo "lint-canonical-domains: FAIL — $VIOLATION_COUNT banned-domain reference(s) in $TARGET_PATH" >&2
echo >&2
cat "$TMP_HITS" >&2
echo >&2
echo "Fix: replace each banned domain with the canonical equivalent from $(basename "$CONFIG_PATH")." >&2
echo "If a hit is a legitimate historical/archival reference, add the file to the EXCLUDE_FILES list" >&2
echo "in scripts/lint-canonical-domains.sh — do NOT remove the entry from banned[]." >&2
exit 1
