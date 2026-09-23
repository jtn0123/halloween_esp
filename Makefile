# The project venv is required for Python recipes. CI passes PY= on the
# command line for those; `make rust` must still parse without a venv.
ifeq ($(origin PY),command line)
else ifneq ($(wildcard .venv/bin/python),)
  PY := .venv/bin/python
else
  PY = $(error .venv is missing — run make setup)
endif
ESPHOME := .venv/bin/esphome
# Every build tree is keyed on this checkout's directory name
# (firmware/build_path.yaml), so a worktree never shares objects with main.
ESPHOME_RUN = $(ESPHOME) -s checkout $(notdir $(CURDIR))
# THE castle build: an Adafruit ESP32-S3 Feather #5477 in the castle-carrier
# v3.3a, which is what is in the yard. This variable has named three files:
# firmware/castle_flash.yaml until 2026-09-01, when the show's two real songs
# put 2.2 MB of audio in an image that has to fit a 1.75 MB OTA slot
# (docs/notes/03-build.md §12.15); firmware/castle_sd.yaml, the ESP32-S2
# Feather, until 2026-09-17, when v5.64 ran on the S3 Feather over Wi-Fi and
# the S2 was retired rather than kept as a second target nobody flashes
# (§12.20). Each time the old file was DELETED, not nursed.
YAML := firmware/castle_feather_s3.yaml
# The ESPHome device name inside $(YAML) — its hostname, its OTA identity and
# the directory its build tree lives in. Named here so `make ota` and the
# image guard can find what `make build` wrote without parsing the YAML.
DEVICE := castle-feather-s3
DEVICE_S3 := castle-s3
# The SECOND build, and deliberately not the default: the ESP32-S3-WROOM-1
# carrier board (castle-carrier v5, docs/V5-SPEC.md §13). It shares every
# line of the show AND the chip with $(YAML), differing only in the board —
# no Feather board definition, 8 MB of flash, no status pixel, an ammeter.
# It has never been on hardware; the weekly CI job compiles it so it cannot
# rot unnoticed.
YAML_S3 := firmware/castle_s3.yaml
# The documented target; pyproject/CI/mypy all say 3.13. Found on PATH rather
# than at one Homebrew path, which is not where every machine keeps it.
# Recursive (=), not :=, so the lookup — and the error — only happen when
# `make setup` expands it, not on every make invocation.
PY_SETUP = $(or $(shell command -v python3.13),$(error python3.13 not found — brew install python@3.13))

.PHONY: show-lab cues build-s3 upload-s3 logs-s3 validate-s3 build-fs3 upload-fs3 logs-fs3 publish ota pycheck test test-fast test-radio lint check check-all e2e help setup audio generate preview build validate upload logs bench bench-logs bench-audio bench-audio-logs track studio clean coverage coverage-gate coverage-radio audit lock lock-hashes sd-build sd-upload rust rust-test rust-lint rust-coverage

help:
	@echo "Halloween Castle"
	@echo ""
	@echo "  make setup      create .venv and install esphome + render deps"
	@echo "  make audio      render scenes/scenes.yaml -> audio/*.mp3"
	@echo "  make cues       render every track's light show to a card cue file (TRACKS=\"a b\" for some)"
	@echo "  make generate   render scenes.yaml -> firmware/generated/scenes.yaml"
	@echo "  make preview    splice scenes + rendered audio into the previewer"
	@echo "  make validate   check the ESPHome config (fast, no toolchain)"
	@echo "  make build      compile $(YAML) (implies audio + generate)"
	@echo "  make upload     compile and flash over the Feather's USB-C"
	@echo "  make logs       tail device logs over the same cable"
	@echo "  make build-s3 / upload-s3 / logs-s3   the same for the WROOM carrier"
	@echo "  make build-fs3 / upload-fs3 / logs-fs3   aliases for build / upload / logs"
	@echo "  make bench      flash the bare-Feather dry run (no parts needed)"
	@echo "  make bench-logs tail the bench build's logs"
	@echo "  make bench-audio  measure decode load on the bare board (no speakers)"
	@echo "  make track SRC=<file|url> ID=<name>   import audio into tracks/"
	@echo "  make studio     serve the cue desk with track management (localhost)"
	@echo "  make publish    push scene tracks + the Castle Radio page to the castle"
	@echo "  make ota        build the firmware and flash that image over HTTP"
	@echo "  make test       python unit tests (~1 min)"
	@echo "  make test-fast  the same minus the slow + Rust suites (inner loop)"
	@echo "  make show-lab   opt-in light-show lab: rebuild the beat-locked candidates and serve"
	@echo "                  the before/after page on 127.0.0.1:8894 (SHOW_LAB_PORT=…); software only"
	@echo "  make test-radio demo/castle-radio: its python suite + its node --test suites"
	@echo "  make rust       build castle-core (release: the binaries the tools spawn)"
	@echo "  make rust-test  cargo test the crate"
	@echo "  make rust-lint  cargo fmt --check + clippy -D warnings"
	@echo "  make lint       ruff + mypy over $(PY_SCOPE), plus rust-lint"
	@echo "  make check      test + test-radio + lint + image/LOC guards + tsc + node suites"
	@echo "                  = CI's blocking python/TS steps; NOT the coverage floors,"
	@echo "                  the esphome builds or the browser suite (see the comment)"
	@echo "  make e2e        browser tests (needs: cd web && npx playwright install chromium)"
	@echo "                  CASTLE_E2E_PORT=8821 make e2e   to run beside another suite"
	@echo "  make check-all  every check, including the browser tests"
	@echo "  make coverage   unit tests under coverage.py, report on tools/ (non-gating)"
	@echo "  make rust-coverage  cargo llvm-cov summary for core/ (non-gating)"
	@echo "  make coverage-gate  the same, failing under $(COVERAGE_MIN)% (what CI enforces)"
	@echo "  make coverage-radio demo/castle-radio under its own floor ($(COVERAGE_RADIO_MIN)%)"
	@echo "  make audit      pip-audit the locked Python deps (non-gating)"
	@echo "  make lock       relock requirements.lock from a clean throwaway venv"
	@echo "  make lock-hashes  refresh the lock's sha256 lines, same pins, no resolve"
	@echo "  make clean      drop firmware/.esphome and rendered wavs"
	@echo "  make sd-build / sd-upload   older names for build / upload"
	@echo "  make bench-audio-logs       tail the bench-audio build's logs"
	@echo ""
	@echo "scenes/scenes.yaml is the source of truth for audio, cues AND the previewer."
	@echo "The castle is $(YAML) (an ESP32-S3 Feather #5477 in"
	@echo "carrier v3.3a) and the show lives on the card: 'make publish' before"
	@echo "'make ota', or the board boots to a chirp."

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
	@# ESPHome (2026.8+) compiles through ccache whenever one is on PATH, with
	@# no configuration: a cold build tree — a fresh worktree's first build,
	@# a wiped one — becomes a cache read instead of ~80 s of xtensa-gcc.
	@command -v ccache > /dev/null \
		|| echo "note: no ccache on PATH — 'brew install ccache' and every cold firmware build after the first is mostly cache hits"
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

# The Rust studio is the studio (grade report 2026-09-01 G1, finished by
# docs/RETIREMENT.md): the launcher builds it when cargo is here and refuses
# with a printed reason when it cannot. The logic lives in the script, not
# here, because .claude/launch.json needs the same decision and cannot
# express it. ARGS passes the studio's own command line through:
# ARGS="8766 --lan".
# Opt-in and offline: candidates are written only under the ignored
# .radio-data/comparison/, never beside a prepared show, and nothing here
# talks to the castle. Adopting a candidate is a separate, deliberate change.
SHOW_LAB_PORT ?= 8894
show-lab:
	@$(PY) demo/castle-radio/show_lab.py
	@echo "open http://127.0.0.1:$(SHOW_LAB_PORT)/show-lab.html   (Ctrl-C stops the server)"
	@$(PY) -m http.server $(SHOW_LAB_PORT) --bind 127.0.0.1 \
		--directory demo/castle-radio/.radio-data/comparison

studio: preview
	@tools/studio_launch.sh $(ARGS)

# The publish chain (grade report 2026-08-23 A1/I4): everything the castle needs after
# a scene edit, in one word. Host resolves via tools/hosts.py (CASTLE_HOST,
# else devices.toml). The studio's rebuild runs the same push automatically;
# this is the terminal spelling. `make ota` builds first and sd_sync stops
# audio before flashing (the standing OTA rule).
#
# `generate` joined the prerequisites in v5.67: a scene's timeline and its
# numbers are card files now (audio/card/scenes/<id>.cue and show.man), so
# publishing without regenerating them would push yesterday's show — the whole
# claim of the change is that a scene edit is a publish, not a flash.
publish: audio generate
	@$(PY) tools/sd_sync.py scenes
	@if ls audio/card/cues/*.cue > /dev/null 2>&1; then $(PY) tools/sd_sync.py cues; fi
	@$(PY) tools/sd_sync.py site

# A song's own light show as a file on the card (firmware 5.63,
# tools/render_cues.py): any song, every beat, no scene slot and no OTA.
#   make cues                 every track in tracks/
#   make cues TRACKS="a b"    just those
# `make publish` pushes what this writes.
cues:
	@$(PY) tools/render_cues.py $(if $(TRACKS),$(TRACKS),--all)

# The image is NAMED, not searched for. sd_sync's own fallback globs
# firmware/.esphome/build/**/firmware.bin, and build_path.yaml has put every
# tree on the external volume since 2026-09-16 — so with no argument this
# target either found nothing or flashed a months-old binary. check_image
# --path is the same derivation the OTA-slot guard uses (and it prefers
# firmware.ota.bin, which is what /api/ota was flashed with on 2026-09-17), so
# there is one answer to "where is the image" and `make ota` sends what
# `make build` just produced.
ota: build
	@$(PY) tools/sd_sync.py ota "$$($(PY) tools/check_image.py $(DEVICE) --path)"

# Kept as aliases, not as a second build. They named the microSD variant back
# when there were two castles to choose between; every build has streamed the
# show off a card since 2026-09-01, so "sd" stopped distinguishing anything and
# these point at the default. Muscle memory and every note that says
# `make sd-build` keep working, and nothing has to be remembered twice.
sd-build: build

sd-upload: upload

bench: audio generate
	$(ESPHOME_RUN) run firmware/bench.yaml

bench-logs:
	$(ESPHOME_RUN) logs firmware/bench.yaml

validate: generate validate-s3
	@$(ESPHOME_RUN) config $(YAML) > /dev/null && echo "config OK"

# The carrier build is validated by the same target, not by a habit anyone
# has to remember: it is the build with no hardware to catch its mistakes.
validate-s3: generate
	@$(ESPHOME_RUN) config $(YAML_S3) > /dev/null && echo "config OK (s3)"
	@$(ESPHOME_RUN) config firmware/castle_s3_qemu.yaml > /dev/null && echo "config OK (s3 qemu)"

# The S3 Feather's USB-C is the chip's own USB Serial/JTAG, so `upload` and
# `logs` share one cable — except the FIRST flash of a factory Feather, which
# wants BOOT held while RESET is tapped (pending/README.md's bring-up list).
build: audio generate
	$(ESPHOME_RUN) compile $(YAML)

upload: audio generate
	$(ESPHOME_RUN) run $(YAML)

logs:
	$(ESPHOME_RUN) logs $(YAML)

# The ESP32-S3 carrier board. Same three verbs, same generated show — the
# only difference is which YAML names the chip. `upload-s3` goes over the
# module's own USB Serial/JTAG: no adapter, and no BOOT-button dance.
build-s3: audio generate
	$(ESPHOME_RUN) compile $(YAML_S3)

upload-s3: audio generate
	$(ESPHOME_RUN) run $(YAML_S3)

logs-s3:
	$(ESPHOME_RUN) logs $(YAML_S3)

# Aliases for the three verbs above, from the fortnight (2026-09-14 to
# 2026-09-17) when the S3 Feather was the THIRD target and `build` still meant
# the S2. It is `build` now, so these point at it — same spirit as sd-build
# below: muscle memory and every note that says `make upload-fs3` keep
# working, and nothing has to be remembered twice.
build-fs3: build

upload-fs3: upload

logs-fs3: logs

clean:
	rm -rf firmware/.esphome audio/*.wav

bench-audio: audio generate
	$(ESPHOME_RUN) run firmware/bench_audio.yaml

bench-audio-logs:
	$(ESPHOME_RUN) logs firmware/bench_audio.yaml

# pyproject.toml says >=3.13; the bare-python3 fallback above could silently
# hand an older interpreter to everything below (grade report 2026-08-23 F5).
pycheck:
	@$(PY) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else \
		(print(f"python {sys.version.split()[0]} is too old — this repo needs 3.13+ (make setup)") or 1))'

test: pycheck
	@$(PY) -m unittest discover -s tests -q

# The inner loop: everything except the suites that exist to wait — the
# castle chaos/relay/protocol fuzz and the generator fuzz spend their time
# in deliberate timeouts and random documents, and the suites that drive the
# studio binary (`_rust`, `_rs`, `castle_core`) spend theirs in cargo, two
# release builds and clippy deep. The Rust work is one word away
# (`make rust-test` / `make rust-lint`), not gone. `make test` before handing
# work back; this while you are still typing.
SLOW_SUITES := chaos|relay|fuzz|_rust|_rs|castle_core|studio
# Castle Radio: the Python suite next to the sources plus the browser
# sources run under node:test (needs node 22, no npm install).
test-radio:
	@$(PY) -m unittest discover -s demo/castle-radio -t demo/castle-radio -p 'test_*.py' -q \
		&& node --test demo/castle-radio/test_castle_radio.test.mjs demo/castle-radio/test_castle_fuzz.test.mjs \
		demo/castle-radio/test_castle_honesty.test.mjs demo/castle-radio/test_desktop_tools.test.mjs demo/castle-radio/test_companion.test.mjs demo/castle-radio/test_device_helper.test.mjs demo/castle-radio/test_card_cues.test.mjs \
		demo/castle-radio/test_rich_preview.test.mjs

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

# Castle Radio's own half of the same question (grade report 2026-09-17 D2).
# Its Python suite lives BESIDE its sources and is not part of the discover
# above, so it gets its own measured run rather than dragging a directory it
# never exercises into the tools/ number: 68% of the radio's non-test sources
# on 2026-09-17, floor one below, and the same ratchet rule — raise it when a
# fresh run beats it. The demo's browser half is node --test, which no
# coverage run here sees.
COVERAGE_RADIO_MIN := 67
coverage-radio:
	$(call NEED_DEV_TOOL,coverage)
	@$(PY) -m coverage run --source=demo/castle-radio -m unittest discover \
		-s demo/castle-radio -t demo/castle-radio -p 'test_*.py' -q
	@$(PY) -m coverage report --skip-empty --omit='*/test_*.py' \
		--fail-under=$(COVERAGE_RADIO_MIN)

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
#
# Both targets end by asking PyPI for the sha256 of every file of every pinned
# version, because a version says nothing about the bytes that arrive and CI
# installs with --require-hashes. `lock-hashes` is only that half: same pins,
# refreshed digests, no resolve and no venv — what to run when a hash is
# missing but nothing should move.
lock:
	@$(PY) tools/lock_deps.py

lock-hashes:
	@$(PY) tools/lock_deps.py --hashes-only

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
# The Python the gate reads. demo/castle-radio joined it on 2026-09-17
# (grade report 2026-09-17 D2) — one scope, one invocation, so its modules
# resolve the tools/ names they import exactly as tests/ does.
PY_SCOPE := tools tests demo/castle-radio
lint: rust-lint
	@$(PY) -m ruff format --check --quiet $(PY_SCOPE) || { echo "formatting drift — run: .venv/bin/python -m ruff format $(PY_SCOPE)"; exit 1; }
	@$(PY) -m ruff check $(PY_SCOPE)
	@$(PY) -m mypy $(PY_SCOPE)

# The hand-back gate. It is CI's blocking PYTHON+TS steps and nothing more —
# `test-radio` joined it on 2026-09-17 because the radio's suites are two CI
# steps of their own and `check` could pass while they were red (grade report
# 2026-09-17 pm D2). What CI has that this does NOT, deliberately:
#   * `coverage-gate` / `coverage-radio` — the same two suites AGAIN under
#     coverage.py, for a floor, at double the wall time. Run them before a
#     release or when you touched tools/ or demo/castle-radio coverage.
#   * `validate` + the two esphome compiles — need the toolchain (`make
#     validate`, `check-all`).
#   * the browser suite — `make e2e` (`check` says so at the end).
#   * the wasm face build and `make audit` (non-gating).
check: audio test test-radio lint
	@$(PY) tools/check_image.py $(DEVICE)
	@$(PY) tools/check_image.py $(DEVICE_S3)
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

check-all: check validate e2e
