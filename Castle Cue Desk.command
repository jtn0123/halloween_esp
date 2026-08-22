#!/bin/bash
# Double-click this to open the cue desk.
#
# A web page cannot start a server that isn't running — nothing in a browser
# can reach a process that doesn't exist yet. So this is the one thing that
# has to happen outside the browser, and it's a double-click rather than a
# command you type. Once it's up, the page has its own Stop and Restart
# controls.
#
# Finder marks downloaded .command files non-executable; if double-clicking
# does nothing, run once:  chmod +x "Castle Cue Desk.command"

cd "$(dirname "$0")" || exit 1

PORT=8765
URL="http://127.0.0.1:$PORT/"

# Already running? Just open it — don't start a second one on a taken port.
if curl -s -m 2 "http://127.0.0.1:$PORT/api/tracks" >/dev/null 2>&1; then
  echo "Cue desk is already running."
  open "$URL"
  exit 0
fi

# The project venv when it exists, else the python3 on PATH — the same rule
# the Makefile and the commit hook use, so a checkout sharing another
# clone's venv via CASTLE_PY still opens.
PY=${CASTLE_PY:-$(command -v .venv/bin/python || command -v python3)}
if [ -z "$PY" ] || ! "$PY" -c "import numpy, yaml" 2>/dev/null; then
  echo "No usable python found (.venv missing?). Run 'make setup' once, then try again."
  read -r -p "Press return to close." _
  exit 1
fi

# Make sure the page exists and matches the current scenes before serving it.
echo "Building the previewer..."
"$PY" tools/gen_previewer.py || {
  echo "Build failed — see above."
  read -r -p "Press return to close." _
  exit 1
}

echo "Starting the cue desk on $URL"
"$PY" tools/studio.py "$PORT" &
SERVER_PID=$!

# Wait for it to answer before opening the browser, so the page never loads
# into a "server not running" state and has to be reloaded.
for _ in $(seq 1 40); do
  curl -s -m 1 "http://127.0.0.1:$PORT/api/tracks" >/dev/null 2>&1 && break
  sleep 0.25
done

open "$URL"
echo
echo "Cue desk running. Close this window or press Ctrl-C to stop it."
wait $SERVER_PID
