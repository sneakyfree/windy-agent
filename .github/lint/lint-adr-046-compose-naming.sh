#!/usr/bin/env bash
# lint-adr-046-compose-naming.sh
# ---------------------------------------------------------------
# Enforces ADR-046: every docker-compose*.yml file in the working tree
# MUST declare a top-level `name:` field, OR carry an explicit
# `# adr-046-exempt: <reason>` comment in the first 10 lines.
#
# Why: directory-name-derived project names cause container collisions
# on multi-service hosts. The full rationale + 2 production incidents
# that drove this decision: see
# ~/kit-army-config/docs/adr-046-compose-project-naming-discipline-2026-05-15.md
#
# Usage:
#   lint-adr-046-compose-naming.sh [path]
#
# Defaults:
#   path    = .
#
# Exit codes:
#   0  clean
#   1  one or more compose files lack `name:` and no exemption
#   2  usage error
#
# Pure shell. No external deps beyond find/grep/head.
# ---------------------------------------------------------------

set -u

TARGET_PATH="${1:-.}"

if [ ! -d "$TARGET_PATH" ]; then
  echo "lint-adr-046: target path '$TARGET_PATH' is not a directory" >&2
  exit 2
fi

# Find compose files. Skip third-party dirs (node_modules, .venv, build, dist)
# and the kit-army-config docs directory (we don't run lint against docs
# that happen to contain compose snippets).
mapfile -t COMPOSE_FILES < <(
  find "$TARGET_PATH" -type f \
    \( -name "docker-compose.yml" -o -name "docker-compose.yaml" \
       -o -name "docker-compose.*.yml" -o -name "docker-compose.*.yaml" \
       -o -name "compose.yml" -o -name "compose.yaml" \) \
    -not -path "*/node_modules/*" \
    -not -path "*/.venv/*" \
    -not -path "*/dist/*" \
    -not -path "*/build/*" \
    -not -path "*/.git/*" \
    2>/dev/null
)

if [ ${#COMPOSE_FILES[@]} -eq 0 ]; then
  # No compose files; nothing to enforce.
  echo "lint-adr-046: no docker-compose files found in $TARGET_PATH — clean."
  exit 0
fi

VIOLATIONS=()
EXEMPT=()
COMPLIANT=()

for f in "${COMPOSE_FILES[@]}"; do
  # Check for exemption comment in the first 10 lines
  if head -10 "$f" | grep -qE "^[[:space:]]*#[[:space:]]*adr-046-exempt:"; then
    EXEMPT+=("$f")
    continue
  fi
  # Check for top-level `name:` field. YAML top-level keys are flush-left.
  # We match `^name:` to avoid false positives on indented `name:` fields
  # (e.g., service-level `container_name:` would NOT match, nor would a
  # `name:` nested inside `services:`).
  if grep -qE "^name:[[:space:]]" "$f"; then
    COMPLIANT+=("$f")
    continue
  fi
  VIOLATIONS+=("$f")
done

# Report
echo "lint-adr-046: scanned ${#COMPOSE_FILES[@]} compose file(s):"
echo "  compliant:  ${#COMPLIANT[@]}"
echo "  exempt:     ${#EXEMPT[@]}"
echo "  violations: ${#VIOLATIONS[@]}"

if [ ${#EXEMPT[@]} -gt 0 ]; then
  echo ""
  echo "Exempt files (carry '# adr-046-exempt:' header):"
  for f in "${EXEMPT[@]}"; do
    reason=$(head -10 "$f" | grep -E "^[[:space:]]*#[[:space:]]*adr-046-exempt:" | head -1 | sed 's/.*adr-046-exempt:[[:space:]]*//')
    echo "  $f → $reason"
  done
fi

if [ ${#VIOLATIONS[@]} -gt 0 ]; then
  echo ""
  echo "lint-adr-046: FAIL — ${#VIOLATIONS[@]} compose file(s) lack an explicit 'name:' field."
  echo ""
  echo "Violations:"
  for f in "${VIOLATIONS[@]}"; do
    echo "  $f"
  done
  echo ""
  echo "Fix: add a top-level 'name: <unique-project-name>' to each file."
  echo "Naming convention (see ADR-046):"
  echo "  - <repo>/deploy-prod/docker-compose.yml  →  name: <svc>-prod"
  echo "  - <repo>/deploy/docker-compose.yml       →  name: <svc>     (production)"
  echo "                                            OR  name: <svc>-launch (dev workspace)"
  echo "  - <repo>/deploy-staging/docker-compose.yml →  name: <svc>-staging"
  echo ""
  echo "Alternative for genuinely dev-only files that never touch multi-service hosts:"
  echo "  Add a header comment: '# adr-046-exempt: <one-line reason>'"
  echo ""
  echo "Reference: ~/kit-army-config/docs/adr-046-compose-project-naming-discipline-2026-05-15.md"
  exit 1
fi

echo ""
echo "lint-adr-046: ✓ all docker-compose files declare 'name:' or are exempt."
exit 0
