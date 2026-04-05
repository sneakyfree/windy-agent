#!/usr/bin/env bash
set -euo pipefail

# Prepare a clean HiFly open-source fork from the Windy Fly repo.
# Usage: ./scripts/prepare-hifly-fork.sh [output_dir]

OUTPUT="${1:-../hifly}"

echo "🪰 Preparing HiFly fork..."
echo "   Source: $(pwd)"
echo "   Output: ${OUTPUT}"
echo ""

# 1. Copy the repo (excluding .git, .venv, data, node_modules)
if [ -d "$OUTPUT" ]; then
    echo "⚠  Output directory exists. Remove it first: rm -rf ${OUTPUT}"
    exit 1
fi

mkdir -p "$OUTPUT"
rsync -a --exclude='.git' --exclude='.venv' --exclude='data' \
    --exclude='node_modules' --exclude='__pycache__' \
    --exclude='dist' --exclude='.env' \
    --exclude='gateway/dashboard/node_modules' \
    . "$OUTPUT/"

cd "$OUTPUT"

echo "✓ Repo copied"

# 2. Remove Windy-exclusive files
rm -rf \
    src/windyfly/eternitas/ \
    src/windyfly/matrix_provision.py \
    src/windyfly/mail_provision.py \
    src/windyfly/mail_mock.py \
    src/windyfly/mail_rate_limiter.py \
    src/windyfly/phone_provision.py \
    src/windyfly/birth_certificate.py \
    src/windyfly/hatch_email.py \
    src/windyfly/hatch_actions.py \
    src/windyfly/ecosystem_health.py \
    src/windyfly/cloud_backup.py \
    src/windyfly/vps_deploy.py \
    src/windyfly/tools/windy_api.py \
    tests/test_eternitas.py \
    tests/test_contract_eternitas.py \
    tests/test_contract_matrix.py \
    tests/test_contract_mail.py \
    tests/test_hatch_actions.py \
    tests/test_hatch_email.py \
    BRAND-ARCHITECTURE.md \
    2>/dev/null || true

echo "✓ Windy-exclusive files removed"

# 3. Rename package: windyfly → hifly
find src -type f -name "*.py" -exec sed -i '' \
    -e 's/from windyfly/from hifly/g' \
    -e 's/import windyfly/import hifly/g' \
    -e 's/windyfly\./hifly./g' \
    {} \;

find tests -type f -name "*.py" -exec sed -i '' \
    -e 's/from windyfly/from hifly/g' \
    -e 's/import windyfly/import hifly/g' \
    -e 's/windyfly\./hifly./g' \
    {} \;

mv src/windyfly src/hifly
echo "✓ Package renamed to hifly"

# 4. Rename user-facing strings
find . -type f \( -name "*.py" -o -name "*.toml" -o -name "*.md" \
    -o -name "*.ts" -o -name "*.tsx" -o -name "*.html" -o -name "*.yml" \) \
    -exec sed -i '' \
    -e 's/Windy Fly/HiFly/g' \
    -e 's/windy-fly/hifly/g' \
    -e 's/windyfly/hifly/g' \
    -e 's/windy-agent/hifly/g' \
    {} \;

echo "✓ Strings renamed"

# 5. Update pyproject.toml
sed -i '' \
    -e 's/name = "windyfly"/name = "hifly"/' \
    -e 's/windy = "windyfly.cli:main"/hifly = "hifly.cli:main"/' \
    -e 's/path = "src\/windyfly\/__init__.py"/path = "src\/hifly\/__init__.py"/' \
    pyproject.toml

echo "✓ pyproject.toml updated"

# 6. Update CLI command name
find src/hifly -name "*.py" -exec sed -i '' \
    -e 's/"windy"/"hifly"/g' \
    -e "s/'windy'/'hifly'/g" \
    {} \;

echo "✓ CLI entry point: hifly"

# 7. Remove ecosystem section from toml
sed -i '' '/^\[ecosystem\]/,/^$/d' hifly.toml 2>/dev/null || true
mv windyfly.toml hifly.toml 2>/dev/null || true

echo "✓ Config cleaned"

# 8. Replace SOUL.md with generic personality
cat > SOUL.md << 'SOUL'
# HiFly Agent Personality

You are HiFly — a personal AI assistant built on the open-source HiFly framework.

## Core Traits
- Helpful, honest, and proactive
- Remembers conversations and learns preferences
- Has personality sliders the user can adjust
- Can use tools: weather, reminders, todos, search, news, calculator

## How You Behave
- Be warm and conversational, not robotic
- When you make mistakes, own them and learn ("Never Wrong Twice")
- Suggest capabilities when relevant, but don't spam
- Respect the user's privacy — their data stays on their machine
SOUL

echo "✓ SOUL.md replaced with generic HiFly personality"

# 9. Add MIT license
cat > LICENSE << 'LICENSE'
MIT License

Copyright (c) 2026 HiFly Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
LICENSE

echo "✓ MIT LICENSE added"

# 10. Init new git repo
git init
git add -A
git commit -m "Initial HiFly release — open-source AI agent framework"

echo ""
echo "✅ HiFly fork ready at: ${OUTPUT}"
echo "   pip install -e ."
echo "   hifly go"
