#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Lydfiler i data/ ==="
for f in data/*.wav data/*.m4a data/*.opus data/*.mp3; do
  [ -e "$f" ] || continue
  echo "--- Fil: $f ---"
  ffprobe -v error -show_entries format=duration,size,bit_rate -of default=noprint_wrappers=1 "$f" || true
  ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,sample_rate,channels -of default=noprint_wrappers=1 "$f" || true
done
