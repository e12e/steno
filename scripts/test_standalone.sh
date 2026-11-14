#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Bygger dist/steno.py..."
python3 scripts/build_single_file.py

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

WATCH_TIMEOUT="${STENO_TEST_TIMEOUT:-}"
if [ -z "$WATCH_TIMEOUT" ]; then
    if [ -n "${CI:-}" ]; then WATCH_TIMEOUT=60; else WATCH_TIMEOUT=15; fi
fi

echo "==> Tester frittstående dist/steno.py og dist/steno.com med ${WATCH_TIMEOUT}s tidsavbrudd-vakt (watcher) i: $TMP_DIR"
cp dist/steno.py "$TMP_DIR/steno.py"
cp dist/steno.com "$TMP_DIR/steno.com"

python3 - "$TMP_DIR/steno.py" "$TMP_DIR/steno.com" "$ROOT_DIR/steno.py" "$ROOT_DIR/dist/SHA256SUMS.txt" "$TMP_DIR/com_install" <<'PY'
import hashlib
import os
import subprocess
import sys
from pathlib import Path

dist_app, dist_com, root_app, sums_file, com_install_dir = sys.argv[1:6]
timeout_sec = int(os.environ.get("STENO_TEST_TIMEOUT", "60" if os.environ.get("CI") else "15"))

com_env = os.environ.copy()
com_env["STENO_INSTALL_DIR"] = com_install_dir

for label, cmd, env in [
    ("dist --self-test (python3)", [sys.executable, dist_app, "--self-test"], None),
    ("dist --help (python3)", [sys.executable, dist_app, "--help"], None),
    ("dist direct ./steno.py --self-test", [dist_app, "--self-test"], None),
    ("dist shell ./steno.com --self-test", ["/bin/sh", "-c", '"$1" --self-test', "_", dist_com], com_env),
    ("root --self-test (python3)", [sys.executable, root_app, "--self-test"], None),
    ("root --help (python3)", [sys.executable, root_app, "--help"], None),
    ("root direct ./steno.py --self-test", [root_app, "--self-test"], None),
]:
    print(f"  [Watcher {timeout_sec}s] Kjører {label}...")
    proc = subprocess.run(cmd, timeout=timeout_sec, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"FEIL i {label} (kode {proc.returncode})")
    if "--self-test" in cmd and "SELF_TEST_OK" not in proc.stdout:
        raise SystemExit(f"FEIL i {label}: mangler SELF_TEST_OK")
    print(f"  [OK] {label} fullført uten heng.")

sums_path = Path(sums_file)
dist_dir = sums_path.parent
for raw_line in sums_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line:
        continue
    expected_sha, name = line.split(None, 1)
    target = dist_dir / name.lstrip("*")
    actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual_sha != expected_sha.lower():
        raise SystemExit(f"FEIL sjekksum for {target}: forventet {expected_sha}, fikk {actual_sha}")
print("  [OK] SHA256SUMS.txt verifisert for alle filer i dist/.")
PY

echo "==> Isolert frittstående test SUKSESS!"
