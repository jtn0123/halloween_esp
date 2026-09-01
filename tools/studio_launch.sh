#!/bin/sh
# Start the cue desk studio — the Rust twin first, the Python one as the
# fallback (grade report 2026-09-01 G1; the flip the migration plan called
# off-season work). Both servers answer the same two tables in docs/API.md
# and take the same command line: [PORT] [--lan]. Arguments pass straight
# through, so `studio_launch.sh 8766 --lan` means the same thing either way.
#
# The order of preference:
#   1. CASTLE_STUDIO=python|rust forces one of them. `rust` execs the binary
#      exactly as built — it neither rebuilds nor falls back, because an
#      explicit ask that cannot be met is an error, not a surprise.
#   2. cargo present  -> build core/target/release/studio and exec it.
#   3. a binary already built (a clone with no rustup) -> exec it.
#   4. otherwise      -> tools/studio.py, with one line saying why.
#
# The exec'd Rust studio spawns Python children for every rebuild, import
# and generator run (core/src/studio_proc.rs py()): CASTLE_PY, else
# <root>/.venv/bin/python, else python3. We cd to the repo root and the
# binary resolves the root from its own path, so the project venv is found
# without anyone setting CASTLE_PY — set it only from a worktree or a CI
# checkout that borrows another tree's venv.
set -eu

unset CDPATH
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
bin=core/target/release/studio
py=$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

case "${CASTLE_STUDIO:-}" in
python)
	echo "studio: CASTLE_STUDIO=python — starting the Python studio" >&2
	exec "$py" tools/studio.py "$@"
	;;
rust)
	[ -x "$bin" ] || {
		echo "studio: CASTLE_STUDIO=rust but $bin is not built — run \`make rust\`" >&2
		exit 1
	}
	exec "$bin" "$@"
	;;
esac

if command -v cargo > /dev/null 2>&1; then
	# Quiet unless it has something to say; a warm tree is a no-op, a cold
	# one is a minute of compiling with cargo's own progress on stderr.
	(cd core && cargo build --release --quiet --bin studio) || {
		echo "studio: cargo build failed (see above) — starting the Python studio" >&2
		exec "$py" tools/studio.py "$@"
	}
fi

if [ -x "$bin" ]; then
	exec "$bin" "$@"
fi

reason=$(command -v cargo > /dev/null 2>&1 \
	&& echo "$bin missing after the build" \
	|| echo "no cargo and no $bin (install rustup: https://rustup.rs)")
echo "studio: $reason — starting the Python studio (tools/studio.py)" >&2
exec "$py" tools/studio.py "$@"
