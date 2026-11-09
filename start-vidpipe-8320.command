#!/bin/bash
# Start the vidpipe web UI on port 8320 (double-click in Finder).
# vidgrab (8310) and audio.cpp (8301) are expected on this Mac; override with
# VIDPIPE_VIDGRAB_URL / VIDPIPE_TTS_BASE_URL / VIDPIPE_SUBTITLE_FONT (e.g. "PingFang TC").
cd "$(dirname "$0")"
export VIDPIPE_SUBTITLE_FONT="${VIDPIPE_SUBTITLE_FONT:-PingFang TC}"
if [ -x .venv/bin/vidpipe ]; then
  VIDPIPE=.venv/bin/vidpipe
elif command -v vidpipe >/dev/null 2>&1; then
  VIDPIPE=vidpipe
else
  echo "vidpipe not found. Run: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi
echo "vidpipe -> http://127.0.0.1:8320"
(sleep 2; open "http://127.0.0.1:8320") &
exec "$VIDPIPE" serve --port 8320 --no-open
