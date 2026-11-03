#!/usr/bin/env bash
# capture.sh — one full-resolution frame per keypress, named nbNNN_pPPP.png
#
# Usage:
#   ./capture.sh 003          start/continue notebook 003 (resumes after last page)
#   ./capture.sh 003 41       start notebook 003 at page 41
#
# Keys (the pedal sends CAPTURE_KEY; find out which with `evtest` or just
# press it in a terminal):
#   CAPTURE_KEY   capture next page
#   r             retake the last page (overwrites it)
#   q             quit
#
# Run ./lock-camera.sh first in every session.
#
# The files this script writes are the originals. The later scripts
# (process.py, export.py) read from this folder and write their results
# elsewhere; nothing ever edits or deletes what's here.

set -euo pipefail

DEV="${CAM_DEV:-/dev/video0}"
# Captures are saved to notebooks/raw/ unless RAW_DIR is set.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW="${RAW_DIR:-$HERE/raw}"
SIZE="3264x2448"
CAPTURE_KEY="${CAPTURE_KEY:-b}"            # set to whatever the pedal sends
RETAKE_KEY="r"
QUIT_KEY="q"

nb="${1:?usage: capture.sh <notebook-id> [start-page]}"
nb=$(printf '%03d' "$((10#$nb))")
out="$RAW/nb$nb"
mkdir -p "$out"

# Resume from the last existing page unless a start page was given.
if [[ -n "${2:-}" ]]; then
  page=$((10#$2))
else
  page=1
  last=$(ls "$out"/nb"${nb}"_p*.png 2>/dev/null | sort | tail -n 1 | sed -E 's/.*_p([0-9]+)\.png$/\1/' || true)
  [[ -n "$last" ]] && page=$((10#$last + 1))
fi

snap() {
  local n=$1
  local f
  f=$(printf '%s/nb%s_p%03d.png' "$out" "$nb" "$n")
  # Opening the device per frame costs ~1 s; exposure is locked so the first
  # frame is as good as any. If the first frame ever comes out dark, add
  #   -vf "select=gte(n\,2)" -vsync vfr
  # before -frames:v to skip two warm-up frames.
  ffmpeg -loglevel error -y \
    -f v4l2 -input_format mjpeg -video_size "$SIZE" -i "$DEV" \
    -frames:v 1 "$f"
  printf '  %s\n' "$(basename "$f")"
}

echo "notebook nb$nb  →  $out"
echo "next page: $page   [$CAPTURE_KEY] capture   [$RETAKE_KEY] retake last   [$QUIT_KEY] quit"

while true; do
  IFS= read -rsn1 key
  # Enter arrives as an empty string; let it count as the capture key if configured.
  [[ -z "$key" && "$CAPTURE_KEY" == "enter" ]] && key="enter"
  case "$key" in
    "$CAPTURE_KEY")
      snap "$page"
      page=$((page + 1))
      ;;
    "$RETAKE_KEY")
      if (( page > 1 )); then
        snap $((page - 1))
      fi
      ;;
    "$QUIT_KEY")
      break
      ;;
  esac
done

echo "stopped; last page captured: $((page - 1))"
