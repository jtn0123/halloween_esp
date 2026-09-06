#!/bin/sh
# Start the cue desk studio — castle-core's `studio` bin, built first when
# there is cargo to build it with.
#
# There were two servers until 2026-09-06 and this script chose between
# them (CASTLE_STUDIO, and a fall-back to tools/studio.py where there was
# no rustup). docs/RETIREMENT.md retired the Python one; there is nothing
# left to choose, so the script's job is now: build if you can, then exec,
# and say plainly why it cannot rather than starting something else.
#
# The command line passes straight through: `studio_launch.sh 8766 --lan`.
#
# The exec'd studio spawns Python children for every rebuild, import and
# generator run (core/src/studio_proc.rs py()): CASTLE_PY, else
# <root>/.venv/bin/python, else python3. We cd to the repo root and the
# binary resolves the root from its own path, so the project venv is found
# without anyone setting CASTLE_PY — set it only from a worktree or a CI
# checkout that borrows another tree's venv.
set -eu

unset CDPATH
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
bin=core/target/release/studio

if command -v cargo > /dev/null 2>&1; then
	# Quiet unless it has something to say; a warm tree is a no-op, a cold
	# one is a minute of compiling with cargo's own progress on stderr.
	(cd core && cargo build --release --quiet --bin studio) || {
		echo "studio: cargo build failed (see above) — fix it rather than serving a stale binary" >&2
		exit 1
	}
fi

[ -x "$bin" ] || {
	echo "studio: no cargo and no $bin to fall back on." >&2
	echo "        Install rustup (https://rustup.rs) and run \`make rust\`." >&2
	exit 1
}

exec "$bin" "$@"
