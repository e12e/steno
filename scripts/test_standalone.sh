#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "==> Tester frittstående distribusjon av dist/mote_app.py i isolert mappe: $TMP_DIR"
cp dist/mote_app.py "$TMP_DIR/mote_app.py"

cd "$TMP_DIR"
python3 mote_app.py --help
echo "==> Isolert frittstående test SUKSESS!"
