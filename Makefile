PY := .venv/bin/python
ESPHOME := .venv/bin/esphome
YAML := firmware/castle.yaml

.PHONY: check help setup audio generate preview build validate upload logs bench bench-logs bench-audio bench-audio-logs track studio clean

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
	@echo ""
	@echo "scenes/scenes.yaml is the source of truth for audio, cues AND the previewer."

setup:
	/opt/homebrew/bin/python3.13 -m venv .venv
	$(PY) -m pip install --quiet --upgrade pip
	.venv/bin/pip install --quiet numpy scipy pyyaml esphome
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

check:
	@$(PY) tools/check_loc.py
	@cd web && npx tsc --noEmit && echo "typecheck OK"
