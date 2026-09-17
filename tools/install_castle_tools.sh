#!/bin/bash
# Explicit one-time setup for the local Castle Radio importer.
set -eu

cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer currently supports macOS."
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Castle Tools currently requires an Apple Silicon Mac (M1 or newer)."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required for the desktop tools: https://brew.sh"
  exit 1
fi

FORMULAE=""
command -v python3.13 >/dev/null 2>&1 || FORMULAE="$FORMULAE python@3.13"
command -v ffmpeg >/dev/null 2>&1 || FORMULAE="$FORMULAE ffmpeg"
command -v rustup >/dev/null 2>&1 || FORMULAE="$FORMULAE rustup"
if [[ -n "$FORMULAE" ]]; then
  echo "Installing desktop system tools:$FORMULAE"
  # This is a space-separated list of fixed formula names, not user input.
  # shellcheck disable=SC2086
  brew install $FORMULAE
fi

if ! command -v rustup >/dev/null 2>&1; then
  RUSTUP_BIN="$(brew --prefix rustup)/bin"
  export PATH="$RUSTUP_BIN:$PATH"
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "rustup was installed but its cargo proxy could not be found."
  exit 1
fi

PYTHON=""
for candidate in python3.13 /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON=$(command -v "$candidate")
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "Python 3.13 was installed but could not be found. Open a new Terminal and retry."
  exit 1
fi

if [[ ! -x .venv-desktop/bin/python ]]; then
  echo "Creating the isolated Castle Radio environment..."
  "$PYTHON" -m venv .venv-desktop
fi

echo "Installing Castle Radio Python tools..."
.venv-desktop/bin/python -m pip install --upgrade pip
.venv-desktop/bin/python -m pip install -r requirements-desktop.txt

echo "Building the audio analyzer..."
(cd core && cargo build --release --bin analyze_track)

echo "Downloading the htdemucs model..."
.venv-desktop/bin/python -c \
  "from demucs.pretrained import get_model; get_model('htdemucs'); print('htdemucs is ready')"

PATH="$PWD/.venv-desktop/bin:$PATH" .venv-desktop/bin/python \
  tools/castle_tools_status.py --human --require-ready
echo
.venv-desktop/bin/python tools/register_castle_launcher.py
echo "Castle Tools are installed. Use Start Mac tools on the castle website."
