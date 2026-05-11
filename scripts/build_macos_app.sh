#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

rm -rf build dist
uv run pyinstaller --clean --noconfirm Daydayup.spec

codesign --force --deep --sign - "dist/Daydayup.app"

ditto -c -k --keepParent "dist/Daydayup.app" "dist/Daydayup-v0.1.0-macos.zip"

echo "Built dist/Daydayup.app"
echo "Built dist/Daydayup-v0.1.0-macos.zip"
