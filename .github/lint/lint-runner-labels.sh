#!/usr/bin/env bash
# lint-runner-labels.sh
# ---------------------------------------------------------------
# Fails if any workflow in .github/workflows/ targets a GitHub
# HOSTED runner (ubuntu-latest / ubuntu-XX.04 / windows-* /
# macos-*). The sneakyfree account is billing-locked (2026-07);
# all CI runs on our self-hosted Kit 0 runners:
#   runs-on: [self-hosted, linux, x64]
#
# MASTER copy: kit-army-config/scripts/lint-runner-labels.sh
# Vendored per repo at .github/lint/lint-runner-labels.sh — the
# same pattern as lint-canonical-domains.sh. Do not edit vendored
# copies; edit the master and re-vendor.
#
# Escape hatch: append  # runner-lint-allow  to a runs-on line for
# a deliberate exception (e.g. disabled CD workflows kept hosted).
#
# Usage:  lint-runner-labels.sh [workflows-dir]   (default .github/workflows)
#
# Exit codes:  0 clean, 1 hosted-runner label found, 2 usage error
# ---------------------------------------------------------------

set -u

DIR="${1:-.github/workflows}"
[ -d "$DIR" ] || { echo "runner-lint: no such directory: $DIR" >&2; exit 2; }

fail=0
for f in "$DIR"/*.yml "$DIR"/*.yaml; do
  [ -f "$f" ] || continue
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    case "$hit" in *"# runner-lint-allow"*) continue ;; esac
    echo "runner-lint: $f: ${hit}" >&2
    fail=1
  done <<EOF
$(grep -nE 'runs-on:.*(ubuntu-latest|ubuntu-2[0-9]\.04|windows-|macos-)' "$f" 2>/dev/null || true)
EOF
done

if [ "$fail" -ne 0 ]; then
  echo "" >&2
  echo "runner-lint FAILED: hosted runners are billing-locked." >&2
  echo "Use:  runs-on: [self-hosted, linux, x64]" >&2
  echo "Runbook: kit-army-config/docs/ci-runner-runbook.md" >&2
  exit 1
fi
echo "runner-lint OK"
