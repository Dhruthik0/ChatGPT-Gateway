#!/usr/bin/env bash
# Launches the dedicated Chrome profile the bridge attaches to.
#
# Chrome >= 136 ignores --remote-debugging-port on the default user-data-dir,
# so a separate profile is mandatory -- you cannot attach to your everyday
# browser. Log into ChatGPT once in this window; cookies persist here.
set -euo pipefail

PROFILE="${AI_BRIDGE_PROFILE:-$HOME/.local/share/ai-bridge/chrome-profile}"
PORT="${AI_BRIDGE_CDP_PORT:-9333}"
CHROME="${AI_BRIDGE_CHROME:-$(command -v google-chrome || command -v google-chrome-stable)}"

mkdir -p "$PROFILE"
chmod 700 "$PROFILE"        # holds live ChatGPT session cookies

exec "$CHROME" \
  --user-data-dir="$PROFILE" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins="http://127.0.0.1:$PORT" \
  --no-first-run --no-default-browser-check \
  "https://chatgpt.com"
