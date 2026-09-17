#!/bin/bash
# One-time registration for an already installed checkout; no package installs.
set -eu
cd "$(dirname "$0")"
if [[ -x .venv-desktop/bin/python ]]; then
  PY=.venv-desktop/bin/python
elif [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi
if "$PY" tools/register_castle_launcher.py; then
  echo "You can close this window. Future startup happens from the website."
else
  echo "Registration failed. See the message above."
fi
read -r -p "Press return to close." _ || true
