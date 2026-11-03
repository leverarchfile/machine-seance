#!/usr/bin/env bash
# lock-camera.sh — pin the IPEVO V4K to fixed manual settings.
#
# Camera settings do not survive unplugging or a reboot, so run this
# before every capture session. Identical frames are the whole point.
#
# Usage:
#   ./lock-camera.sh --list    show every control the camera exposes, with ranges
#   ./lock-camera.sh           apply the values in the VALUES block below
#
# First-time setup, once the camera is on the rig:
#   1. ./lock-camera.sh --list
#   2. Open a live preview:  mpv av://v4l2:/dev/video0 --profile=low-latency
#      (or guvcview) and adjust focus / exposure / white balance until the
#      page looks right, using v4l2-ctl --set-ctrl=... by hand.
#   3. Read the final numbers back with v4l2-ctl --get-ctrl / --list-ctrls
#      and paste them into VALUES. Commit the file.

set -euo pipefail

# Use the stable by-id path once you know it (ls /dev/v4l/by-id/), e.g.
#   /dev/v4l/by-id/usb-IPEVO_V4K-video-index0
# so a second camera or a reboot can't shuffle /dev/video0 under you.
DEV="${CAM_DEV:-/dev/video0}"

if [[ "${1:-}" == "--list" ]]; then
  echo "== controls =="
  v4l2-ctl -d "$DEV" --list-ctrls-menus
  echo
  echo "== formats =="
  v4l2-ctl -d "$DEV" --list-formats-ext
  exit 0
fi

# ---------------------------------------------------------------------------
# VALUES — fill in after first setup. Leave GAIN and SHARPNESS at 0:
# gain adds noise, sharpening adds halos, and both would be learned as style.
# ---------------------------------------------------------------------------
FOCUS=""        # focus_absolute          (e.g. 120)
EXPOSURE=""     # exposure_time_absolute  (e.g. 312)
WB=""           # white_balance_temperature, kelvin (e.g. 4000) — match your lamps
BRIGHTNESS=""   # optional; leave blank to keep the camera default
CONTRAST=""     # optional
SATURATION=""   # optional
GAIN=0
SHARPNESS=0

if [[ -z "$FOCUS" || -z "$EXPOSURE" || -z "$WB" ]]; then
  echo "lock-camera.sh: fill in FOCUS, EXPOSURE and WB first (run with --list to see ranges)" >&2
  exit 1
fi

# Control names differ between kernel versions; try each alias in turn and
# ignore the ones this camera doesn't have.
set_ctrl() {
  local val=$1; shift
  for name in "$@"; do
    if v4l2-ctl -d "$DEV" --set-ctrl="$name=$val" 2>/dev/null; then
      return 0
    fi
  done
  echo "warning: no control matched [$*] — check --list" >&2
}

# 1. Turn the automatics off.
set_ctrl 0 focus_automatic_continuous focus_auto
set_ctrl 1 auto_exposure exposure_auto              # 1 = manual mode in UVC
set_ctrl 0 white_balance_automatic white_balance_temperature_auto
set_ctrl 0 exposure_dynamic_framerate exposure_auto_priority

# 2. Pin the values.
set_ctrl "$FOCUS"    focus_absolute
set_ctrl "$EXPOSURE" exposure_time_absolute exposure_absolute
set_ctrl "$WB"       white_balance_temperature
set_ctrl "$GAIN"     gain
set_ctrl "$SHARPNESS" sharpness
[[ -n "$BRIGHTNESS" ]] && set_ctrl "$BRIGHTNESS" brightness
[[ -n "$CONTRAST"   ]] && set_ctrl "$CONTRAST"   contrast
[[ -n "$SATURATION" ]] && set_ctrl "$SATURATION" saturation

# 3. Print what the camera actually reports, so drift is visible.
echo "camera locked ($DEV):"
v4l2-ctl -d "$DEV" --list-ctrls | grep -E 'focus|exposure|white_balance|gain|sharpness|brightness|contrast|saturation' || true
