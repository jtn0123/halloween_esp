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

.PHONY: test lint check check-all e2e help setup audio generate preview build validate upload logs bench bench-logs bench-audio bench-audio-logs track studio clean coverage audit lock sd-build sd-upload

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
	@echo "  make studio     serve the cue desk with track management (localhost)"
	@echo "  make test       python unit tests (~1 min)"
	@echo "  make lint       ruff + mypy over tools/ and tests/"
	@echo "  make check      test + lint + image/LOC guards + tsc + node suites"
	@echo "  make e2e        browser tests (needs: cd web && npx playwright install chromium)"
	@echo "                  CASTLE_E2E_PORT=8821 make e2e   to run beside another suite"
	@echo "  make check-all  every check, including the browser tests"
	@echo "  make coverage   unit tests under coverage.py, report on tools/ (non-gating)"
	@echo "  make audit      pip-audit the locked Python deps (non-gating)"
	@echo "  make lock       refreeze requirements.lock from .venv"
	@echo "  make clean      drop firmware/.esphome and rendered wavs"
	@echo "  make sd-build / sd-upload   EXPERIMENTAL microSD variant (PROJECT_NOTES §12.9)"
	@echo "  make bench-audio-logs       tail the bench-audio build's logs"
	@echo ""
	@echo "scenes/scenes.yaml is the source of truth for audio, cues AND the previewer."

setup:
	$(PY_SETUP) -m venv .venv
	.venv/bin/python -m pip install --quiet --upgrade pip
	.venv/bin/pip install --quiet -r requirements.txt -r requirements-dev.txt
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

studio: preview
	@$(PY) tools/studio.py

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

test:
	@$(PY) -m unittest discover -s tests -q

# Where the unit suite reaches and where it does not. Informational — no
# threshold fails it; the number is for deciding what to test next.
coverage:
	@$(PY) -m coverage run --source=tools -m unittest discover -s tests -q
	@$(PY) -m coverage report --include='tools/*' --skip-empty

# Known advisories against what the venv actually has. Non-gating: the hits
# so far are in ESPHome's build toolchain (platformio -> starlette, which never
# sees network input here — ignored by id below) and cryptography 49, which
# the esphome pin drags in and a pin bump will clear. Re-run after `make lock`.
STARLETTE_TOOLCHAIN_ONLY := PYSEC-2026-161 PYSEC-2026-248 PYSEC-2026-249 PYSEC-2026-2280 PYSEC-2026-2281
audit:
	@$(PY) -m pip_audit -r requirements.lock --no-deps --progress-spinner off \
		$(foreach v,$(STARLETTE_TOOLCHAIN_ONLY),--ignore-vuln $(v)) \
		|| echo "(advisories above are informational — see the comment on this target)"

# The lock is the venv as it is: every package, pinned. requirements.txt says
# what the project needs; this says exactly what was tested.
lock:
	@$(PY) -m pip freeze --exclude-editable > requirements.lock
	@echo "requirements.lock: $$(wc -l < requirements.lock) pins"

# Lint + type-check the Python half; config lives in pyproject.toml. The TS
# half's equivalent is the tsc line in `check`.
lint:
	@.venv/bin/ruff check tools tests
	@.venv/bin/mypy tools tests

check: test lint
	@$(PY) tools/check_image.py castle-sd
	@$(PY) tools/check_loc.py
	@cd web && npx tsc --noEmit && echo "typecheck OK"
	@cd web && npm run --silent test

# Browser tests. Separate from `check` because they need a built page and a
# browser binary, and they take an order of magnitude longer than everything
# else put together. They drive the real studio server against a scratch
# tracks directory, and Chromium runs with --mute-audio, so a run is silent.
e2e: preview
	@cd web && npx playwright test

check-all: check e2e
