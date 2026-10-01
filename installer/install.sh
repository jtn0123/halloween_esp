#!/bin/sh
# Castle Tools installer for macOS (and Linux, where the release has no
# prebuilt castle-core: pass --from-source and have cargo).
#
# This script only bootstraps: uv (Astral's official installer, told not to
# edit your shell profile), a uv-managed Python 3.13, and then
# tools/desktop_install.py under that Python, which does the real work for
# every platform. Every flag passes straight through:
#
#   sh installer/install.sh                 install or resume
#   sh installer/install.sh --repair        reinstall the env, re-fetch tools
#   sh installer/install.sh --update        newest GitHub Release, if newer
#   sh installer/install.sh --uninstall     remove the app, keep your songs
#   sh installer/install.sh --uninstall --purge   ...and your songs too
#   sh installer/install.sh --from-source   build castle-core with cargo
#   sh installer/install.sh --dry-run       print the plan, change nothing
#   sh installer/install.sh --help          everything else
#
# Works from an extracted release zip or a git clone alike.
set -eu

unset CDPATH
src=$(cd -- "$(dirname -- "$0")/.." && pwd)
installer="$src/tools/desktop_install.py"
[ -f "$installer" ] || {
	echo "install.sh: $installer is missing — run this from an unpacked Castle Tools folder." >&2
	exit 1
}

dry_run=0
for arg in "$@"; do
	[ "$arg" = "--dry-run" ] && dry_run=1
done

find_uv() {
	if command -v uv > /dev/null 2>&1; then
		command -v uv
	elif [ -x "$HOME/.local/bin/uv" ]; then
		echo "$HOME/.local/bin/uv"
	elif [ -x "$HOME/.cargo/bin/uv" ]; then
		echo "$HOME/.cargo/bin/uv"
	fi
}

uv=$(find_uv)
if [ -z "$uv" ]; then
	if [ "$dry_run" = 1 ]; then
		echo "[dry-run] would install uv from https://astral.sh/uv/install.sh, then continue"
		exit 0
	fi
	command -v curl > /dev/null 2>&1 || {
		echo "install.sh: curl is needed to fetch uv." >&2
		exit 1
	}
	echo "Installing uv (https://docs.astral.sh/uv/)..."
	curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
	uv=$(find_uv)
	[ -n "$uv" ] || {
		echo "install.sh: uv installed but cannot be found; open a new Terminal and retry." >&2
		exit 1
	}
fi

if [ "$dry_run" = 1 ]; then
	echo "[dry-run] would run: $uv python install 3.13"
else
	"$uv" python install 3.13
fi
python=$("$uv" python find --managed-python 3.13 2> /dev/null || "$uv" python find 3.13)
exec "$python" "$installer" --uv "$uv" --source "$src" "$@"
