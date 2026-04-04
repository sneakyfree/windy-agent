#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/release.sh 0.6.0

VERSION="${1:?Usage: ./scripts/release.sh <version>}"

echo "🪰 Releasing Windy Fly v${VERSION}"
echo ""

# 1. Update version in __init__.py
sed -i '' "s/__version__ = \".*\"/__version__ = \"${VERSION}\"/" src/windyfly/__init__.py
echo "✓ Version bumped to ${VERSION}"

# 2. Verify it builds
echo "  Building..."
uv build
echo "✓ Package built"

# 3. Run tests
echo "  Running tests..."
uv run pytest tests/ -v --tb=short
echo "✓ Tests passed"

# 4. Commit version bump
git add src/windyfly/__init__.py
git commit -m "release: v${VERSION}"
echo "✓ Version commit created"

# 5. Tag
git tag "v${VERSION}" -m "Release v${VERSION}"
echo "✓ Tag v${VERSION} created"

# 6. Push (triggers the release.yml workflow → PyPI publish)
git push && git push --tags

echo ""
echo "✅ v${VERSION} pushed! GitHub Actions will:"
echo "   1. Run tests"
echo "   2. Publish to PyPI"
echo "   3. Create GitHub Release"
echo ""
echo "Users will see the update on next 'windy version' or 'windy update'."
