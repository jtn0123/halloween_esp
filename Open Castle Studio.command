#!/bin/bash
# Everyday Castle Radio launcher. It checks, starts, and opens; it never installs.
set -u

cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"
PORT=${CASTLE_STUDIO_PORT:-8871}
URL="http://127.0.0.1:$PORT/"
IDENTITY="${URL}radio/tools"
DEVICE_URL="http://${CASTLE_RADIO_HOST:-10.27.27.81}/"

pause_on_error() {
  echo
  read -r -p "Press return to close." _
  exit 1
}

if [ -x .venv-desktop/bin/python ]; then
  PY="$PWD/.venv-desktop/bin/python"
elif [ -x .venv/bin/python ]; then
  PY="$PWD/.venv/bin/python"
else
  echo "Castle Tools are not installed. Run this once in Terminal:"
  echo "  ./tools/install_castle_tools.sh"
  pause_on_error
fi
PY_BIN=$(dirname "$PY")
export PATH="$PY_BIN:$PATH"

is_castle_radio() {
  curl -fsS -m 2 "$IDENTITY" 2>/dev/null | "$PY" -c \
    'import json,sys; d=json.load(sys.stdin); raise SystemExit(not (d.get("service")=="castle-radio" and d.get("protocol")==1))' \
    >/dev/null 2>&1
}

if is_castle_radio; then
  echo "Castle Studio is already running."
  [ "${CASTLE_STUDIO_NO_BROWSER:-0}" = "1" ] || open "$DEVICE_URL"
  exit 0
fi

if curl -sS -m 2 "$URL" >/dev/null 2>&1; then
  echo "Port $PORT is being used by another app. Close it, then try again."
  pause_on_error
fi

echo "Checking Castle Tools..."
if ! "$PY" tools/castle_tools_status.py --human --require-core; then
  echo
  echo "A required tool is missing. Run this once in Terminal:"
  echo "  ./tools/install_castle_tools.sh"
  pause_on_error
fi

# Built-in demo audio is a local runtime copy. Never overwrite an existing copy.
mkdir -p demo/castle-radio/media
for source in audio/0[1-9]_*.mp3 audio/10_*.mp3; do
  [ -e "$source" ] || continue
  target="demo/castle-radio/media/$(basename "$source")"
  [ -e "$target" ] || cp "$source" "$target"
done

echo "Starting Castle Studio at $URL"
"$PY" demo/castle-radio/server.py "$PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 60); do
  if is_castle_radio; then
    [ "${CASTLE_STUDIO_NO_BROWSER:-0}" = "1" ] || open "$DEVICE_URL"
    echo
    echo "Castle Studio is running. Close this window or press Ctrl-C to stop it."
    wait "$SERVER_PID"
    exit $?
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 0.25
done

echo "Castle Studio did not start. See the message above."
pause_on_error
