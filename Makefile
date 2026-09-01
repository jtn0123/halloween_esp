# The project venv when it exists, else whatever python3 is on PATH (CI
# installs into the runner's interpreter). `make setup` names .venv outright
# below — this fallback must never point a fresh install at the system python.
PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
ESPHOME := .venv/bin/esphome
YAML := firmware/castle_flash.yaml
# The documented target; pyproject/CI/mypy all say 3.13. Found on PATH rather
# than at one Homebrew path, which is not where every machine keeps it.
# Recursive (=), not :=, so the lookup — and the error — only happen when
# `make setup` expands it, not on every make invocation.
PY_SETUP = $(or $(shell command -v python3.13),$(error python3.13 not found — brew install python@3.13))

.PHONY: publish ota pycheck test test-fast lint check check-all e2e help setup audio generate preview build validate upload logs bench bench-logs bench-audio bench-audio-logs track studio clean coverage coverage-gate audit lock sd-build sd-upload rust rust-test rust-lint rust-coverage

help:
	@echo "Halloween Castle"
	@echo ""
	@echo "  make setup      create .venv and install esphome + render deps"
	@echo "  make audio      render scenes/scenes.yaml -> audio/*.mp3"
	@echo "  make generate   render scenes.yaml -> firmware/generated/scenes.yaml"
	@echo "  make preview    splice scenes + rendered audio into the previewer"
	@echo "  make validate   check the ESPHome config (fast, no toolchain)"
	@echo "  make build      compile firmware (implies audio + generate)"
	@echo "  make upload     compile and flash over USB"
	@echo "  make logs       tail device logs"
	@echo "  make bench      flash the bare-Feather dry run (no parts needed)"
	@echo "  make bench-logs tail the bench build's logs"
	@echo "  make bench-audio  measure decode load on the bare board (no speakers)"
	@echo "  make track SRC=<file|url> ID=<name>   import audio into tracks/"
	@echo "  make studio     serve the cue desk with track management (Rust, localhost)"
	@echo "  make publish    push scene tracks + the lean desk page to the castle"
	@echo "  make ota        build the SD firmware and flash it over HTTP"
	@echo "  make test       python unit tests (~1 min)"
	@echo "  make test-fast  the same minus the slow + Rust suites (inner loop)"
	@echo "  make rust       build castle-core (release: the binaries the tools spawn)"
	@echo "  make rust-test  cargo test the crate"
	@echo "  make rust-lint  cargo fmt --check + clippy -D warnings"
	@echo "  make lint       ruff + mypy over tools/ and tests/, plus rust-lint"
	@echo "  make check      test + lint + image/LOC guards + tsc + node suites"
	@echo "  make e2e        browser tests (needs: cd web && npx playwright install chromium)"
	@echo "                  CASTLE_E2E_PORT=8821 make e2e   to run beside another suite"
	@echo "  make check-all  every check, including the browser tests"
	@echo "  make coverage   unit tests under coverage.py, report on tools/ (non-gating)"
	@echo "  make rust-coverage  cargo llvm-cov summary for core/ (non-gating)"
	@echo "  make coverage-gate  the same, failing under $(COVERAGE_MIN)% (what CI enforces)"
	@echo "  make audit      pip-audit the locked Python deps (non-gating)"
	@echo "  make lock       relock requirements.lock from a clean throwaway venv"
	@echo "  make clean      drop firmware/.esphome and rendered wavs"
	@echo "  make sd-build / sd-upload   EXPERIMENTAL microSD variant (PROJECT_NOTES §12.9)"
	@echo "  make bench-audio-logs       tail the bench-audio build's logs"
	@echo ""
	@echo "scenes/scenes.yaml is the source of truth for audio, cues AND the previewer."

setup:
	$(PY_SETUP) -m venv .venv
	.venv/bin/python -m pip install --quiet --upgrade pip
	.venv/bin/pip install --quiet -r requirements.txt -r requirements-dev.txt
	@git config core.hooksPath githooks && echo "pre-commit hook: githooks/"
	@# castle-core is Rust and this target cannot install it (rustup is its own
	@# installer, and silently curl|sh-ing one is not this repo's style). Say so
	@# instead of letting `make audio` be the thing that discovers it: without
	@# cargo, render_audio.py hard-stops rather than falling back to the
	@# machine-dependent Python reference. (grade report 2026-08-31 H3)
	@command -v cargo > /dev/null \
		|| echo "note: no cargo on PATH — castle-core (core/) cannot build, so 'make audio', the importer and the Rust gates will not run. Install rustup: https://rustup.rs"
	@echo "ready. 'make build' next."

audio:
	@$(PY) tools/render_audio.py

generate:
	@$(PY) tools/gen_esphome.py

preview: audio
	@$(PY) tools/gen_previewer.py

# make track SRC=~/Music/thing.wav ID=organ_loop [ARGS="--take 24"]
track:
	@test -n "$(SRC)" || (echo "usage: make track SRC=<file|url> [ID=<name>] [ARGS=...]"; exit 1)
	@$(PY) tools/import_track.py "$(SRC)" $(if $(ID),--id $(ID),) $(ARGS)

# The Rust studio is what this starts now (grade report 2026-09-01 G1): the
# launcher builds it when cargo is here and falls back to tools/studio.py with
# a printed reason when it is not. The logic lives in the script, not here,
# because .claude/launch.json needs the same decision and cannot express it.
# ARGS passes the studio's own command line through: ARGS="8766 --lan".
studio: preview
	@tools/studio_launch.sh $(ARGS)

# The publish chain (grade report 2026-08-23 A1/I4): everything the castle needs after
# a scene edit, in one word. Host resolves via tools/hosts.py (CASTLE_HOST,
# else devices.toml). The studio's rebuild runs the same push automatically;
# this is the terminal spelling. `make ota` builds first and sd_sync stops
# audio before flashing (the standing OTA rule).
publish: preview
	@$(PY) tools/sd_sync.py scenes
	@$(PY) tools/sd_sync.py site

ota: sd-build
	@$(PY) tools/sd_sync.py ota

# EXPERIMENTAL microSD variant — see PROJECT_NOTES §12.9 before relying on it.
sd-build: audio generate
	$(ESPHOME) compile firmware/castle_sd.yaml

sd-upload: audio generate
	$(ESPHOME) run firmware/castle_sd.yaml

bench: audio generate
	$(ESPHOME) run firmware/bench.yaml

bench-logs:
	$(ESPHOME) logs firmware/bench.yaml

validate: generate
	@$(ESPHOME) config $(YAML) > /dev/null && echo "config OK"

build: audio generate
	$(ESPHOME) compile $(YAML)

upload: audio generate
	$(ESPHOME) run $(YAML)

logs:
	$(ESPHOME) logs $(YAML)

clean:
	rm -rf firmware/.esphome audio/*.wav

bench-audio: audio generate
	$(ESPHOME) run firmware/bench_audio.yaml

bench-audio-logs:
	$(ESPHOME) logs firmware/bench_audio.yaml

# pyproject.toml says >=3.13; the bare-python3 fallback above could silently
# hand an older interpreter to everything below (grade report 2026-08-23 F5).
pycheck:
	@$(PY) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else \
		(print(f"python {sys.version.split()[0]} is too old — this repo needs 3.13+ (make setup)") or 1))'

test: pycheck
	@$(PY) -m unittest discover -s tests -q

# The inner loop: everything except the suites that exist to wait — the
# castle chaos/relay/protocol fuzz and the generator fuzz spend their time
# in deliberate timeouts and random documents, and the Rust suites (`_rust`,
# `castle_core`) spend theirs in cargo, two release builds and clippy deep.
# The Rust work is one word away (`make rust-test` / `make rust-lint`), not
# gone. `make test` before handing work back; this while you are still typing.
SLOW_SUITES := chaos|relay|fuzz|_rust|castle_core
test-fast:
	@$(PY) -m unittest -q $$(cd tests && /bin/ls test_*.py | grep -vE '$(SLOW_SUITES)' \
		| sed 's/\.py$$//; s/^/tests./')

# Where the unit suite reaches and where it does not. Informational here —
# the number is for deciding what to test next. `coverage-gate` is the same
# run with the floor CI enforces (COVERAGE_MIN); raise it as coverage lands.
# Measured 83% on 2026-08-23 — the floor is the measurement minus one, and
# it moves UP whenever a fresh `make coverage` beats it (grade report 2026-08-23 D1).
#
# SCOPE: this number describes `tools/` ONLY. The Rust half (core/, the
# production renderer and importer) is outside `--source=tools` entirely, so
# 82% is 82% of a shrinking fraction of the shipped code. `make rust-coverage`
# reports the other half, non-gating (grade report 2026-08-31 D7).
COVERAGE_MIN := 82
# Both tools live in requirements-dev.txt; a venv from before they were added
# dies with "No module named …", which reads as breakage instead of what it
# is — a stale venv. Say so. (grade report 2026-08-24 I2)
NEED_DEV_TOOL = @$(PY) -c "import $(1)" 2>/dev/null \
	|| { echo "$(1) missing — .venv predates a dev dependency; run 'make setup'"; exit 1; }
coverage:
	$(call NEED_DEV_TOOL,coverage)
	@$(PY) -m coverage run --source=tools -m unittest discover -s tests -q
	@$(PY) -m coverage report --include='tools/*' --skip-empty

coverage-gate: coverage
	@$(PY) -m coverage report --include='tools/*' --skip-empty \
		--fail-under=$(COVERAGE_MIN) > /dev/null && echo "coverage >= $(COVERAGE_MIN)%"

# Known advisories against what the venv actually has. Non-gating. The
# exception list lives in .pip-audit-ignore — one id per line WITH its reason
# and a review date (grade report 2026-08-23 E1) — so the "why" survives longer than
# anyone's memory. Re-run after `make lock`.
AUDIT_IGNORES := $(shell awk '/^[A-Z]/{print "--ignore-vuln " $$1}' .pip-audit-ignore)
audit:
	$(call NEED_DEV_TOOL,pip_audit)
	@$(PY) -m pip_audit -r requirements.lock --no-deps --progress-spinner off \
		$(AUDIT_IGNORES) \
		|| echo "(advisories above are informational — see .pip-audit-ignore)"

# The lock is what a CLEAN install of requirements.txt + requirements-dev.txt
# resolves to — NOT this venv. Freezing the live venv wrote the optional
# demucs/torch stack into the file CI installs, and dropped the darwin markers
# freeze cannot know about. tools/lock_deps.py builds a throwaway venv,
# freezes that, and puts the markers (and the subprocess-only pins) back.
# Takes a minute or two: it is a real install, on purpose. Re-run `make audit`
# after.
lock:
	@$(PY) tools/lock_deps.py

# castle-core, the Rust half — 9k lines that had no spelling here at all
# (grade report 2026-08-31 I1). These three ARE the Rust gate: tests/test_castle_core.py
# shells out to them, so the definition lives in one place and `make rust-lint`
# is exactly what the suite and the CI job check.
#
# `cd core` rather than --manifest-path, and it is load-bearing: rustup finds
# rust-toolchain.toml by WORKING DIRECTORY, not by manifest. Run from the repo
# root, the pin (core/rust-toolchain.toml, grade report 2026-08-31 F3) is silently
# ignored and the gate floats on whatever rustc is default.
#
# Optional-toolchain guard, same shape as the pre-commit hook's node_modules
# check: a Python-only clone still gets a green `make check`, with a sentence
# saying what it did not run. CI asserts cargo is present (test_castle_core).
HAVE_CARGO = @command -v cargo > /dev/null || { echo "no cargo — skipping $@ (install rustup: https://rustup.rs)"; exit 0; };

rust:
	$(HAVE_CARGO) cd core && cargo build --release --quiet

rust-test:
	$(HAVE_CARGO) cd core && cargo test --release --quiet

# The Rust side of the coverage question (grade report 2026-08-31 D7). Non-gating, the
# same shape as `make audit`: cargo-llvm-cov is a separate install, so say
# what is missing instead of failing a clone that never asked for it. No
# ratchet here on purpose — this number exists to be looked at while the port
# is still moving, not to block a commit.
rust-coverage:
	$(HAVE_CARGO) command -v cargo-llvm-cov > /dev/null \
		|| { echo "cargo-llvm-cov not installed — 'cargo install cargo-llvm-cov' for the Rust coverage summary"; exit 0; }; \
		cd core && cargo llvm-cov --summary-only

rust-lint:
	$(HAVE_CARGO) cd core && { cargo fmt --check \
		|| { echo "rustfmt drift — run: cd core && cargo fmt"; exit 1; }; }
	$(HAVE_CARGO) cd core && cargo clippy --quiet --all-targets -- -D warnings

# Lint + type-check the Python half; config lives in pyproject.toml. The TS
# half's equivalent is the tsc line in `check`, the Rust half's is rust-lint.
lint: rust-lint
	@$(PY) -m ruff format --check --quiet tools tests || { echo "formatting drift — run: .venv/bin/python -m ruff format tools tests"; exit 1; }
	@$(PY) -m ruff check tools tests
	@$(PY) -m mypy tools tests

check: audio test lint
	@$(PY) tools/check_image.py castle-sd
	@$(PY) tools/check_loc.py
	@$(PY) tools/check_citations.py
	@cd web && npx tsc --noEmit && echo "typecheck OK"
	@cd web && npm run --silent test
	@echo "note: the browser e2e suite did NOT run — 'make e2e' (or 'make check-all') covers the UI"

# Browser tests. Separate from `check` because they need a built page and a
# browser binary, and they take an order of magnitude longer than everything
# else put together. They drive the real studio server against a scratch
# tracks directory, and Chromium runs with --mute-audio, so a run is silent.
# `playwright install chromium` is idempotent and near-instant once the
# browser is cached — running it here turns the two tribal setup steps
# ("build the page, install the browser") into the target itself.
e2e: preview
	@cd web && node -e "require('@playwright/test')" 2>/dev/null \
		|| { echo "e2e needs its deps first: cd web && npm ci"; exit 1; }
	@if command -v cargo >/dev/null 2>&1; then \
		(cd core && cargo build --release --quiet --bin studio) \
			|| { echo "e2e: the Rust studio failed to build — fix it rather than testing a stale binary"; exit 1; }; \
	fi
	@cd web && npx playwright install chromium
	@cd web && npx playwright test

check-all: check e2e
