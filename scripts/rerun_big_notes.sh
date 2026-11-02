#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

AUDIO_FILE="${1:-opptak/møte.wav}"
SPEAKERS="${2:-Kari Nordmann, Ola Hansen, Ingrid Berg, Per Johansen}"

echo "==> Gjenopptar sjekkpunkt for $AUDIO_FILE fra trinn 4 med gitte deltakernavn..."
python3 transcribe.py "$AUDIO_FILE" \
  --task from_4_names \
  --speaker-names "$SPEAKERS" \
  "${@:3}"
