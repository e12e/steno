#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Gjenopptar sjekkpunkt for opptak/møte.wav fra trinn 4 med gitte deltakernavn..."
python3 transcribe.py opptak/møte.wav \
  --task from_4_names \
  --speaker-names "Kari Nordmann, Ola Hansen, Ingrid Berg, Per Johansen" \
  "$@"
