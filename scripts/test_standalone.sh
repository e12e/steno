#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Bygger dist/steno.py..."
python3 scripts/build_single_file.py

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "==> Tester frittstående dist/steno.py med 15s tidsavbrudd-vakt (watcher) i: $TMP_DIR"
cp dist/steno.py "$TMP_DIR/steno.py"

python3 - "$TMP_DIR/steno.py" "$ROOT_DIR/steno.py" <<'PY'
import subprocess
import sys

dist_app, root_app = sys.argv[1], sys.argv[2]

for label, cmd in [
    ("dist --self-test (python3)", [sys.executable, dist_app, "--self-test"]),
    ("dist --help (python3)", [sys.executable, dist_app, "--help"]),
    ("dist direct ./steno.py --self-test", [dist_app, "--self-test"]),
    ("root --self-test (python3)", [sys.executable, root_app, "--self-test"]),
    ("root --help (python3)", [sys.executable, root_app, "--help"]),
    ("root direct ./steno.py --self-test", [root_app, "--self-test"]),
]:
    print(f"  [Watcher 15s] Kjører {label}...")
    proc = subprocess.run(cmd, timeout=15, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"FEIL i {label} (kode {proc.returncode})")
    print(f"  [OK] {label} fullført uten heng.")
PY

echo "==> Isolert frittstående test SUKSESS!"
