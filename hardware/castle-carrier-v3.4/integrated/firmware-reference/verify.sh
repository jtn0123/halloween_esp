#!/usr/bin/env bash
# Compile an isolated Integrated image. Dummy credentials; never flash a device.
set -euo pipefail
cd "$(dirname "$0")/.."
source_repo="${1:-$(cd ../../.. && pwd)}"
build_root="$PWD/local-checks/firmware"
export ESPHOME_DATA_DIR="$build_root/esphome-data"
export CCACHE_DIR="${CCACHE_DIR:-$build_root/ccache}"
export ESPHOME_ESP_IDF_PREFIX="${ESPHOME_ESP_IDF_PREFIX:-$build_root/idf-cache}"
export PLATFORMIO_CORE_DIR="${PLATFORMIO_CORE_DIR:-$build_root/platformio}"
python3 - "$source_repo" "$PWD" <<'PY'
import shutil, sys
from pathlib import Path
source, board = map(Path, sys.argv[1:])
root = board/'local-checks/firmware'
root.mkdir(parents=True, exist_ok=True)
shutil.rmtree(root/'firmware', ignore_errors=True)
shutil.copytree(source/'firmware', root/'firmware', ignore=shutil.ignore_patterns('secrets.yaml', '.esphome', '__pycache__'))
(root/'audio').mkdir(exist_ok=True)
shutil.copy2(source/'audio/00_chirp.mp3', root/'audio/00_chirp.mp3')
(root/'firmware/secrets.yaml').write_text('wifi_ssid: "pcb-validation-only"\nwifi_password: "not-a-real-password"\n', encoding='utf-8')
shutil.copy2(board/'firmware-reference/castle_v34.yaml', root/'firmware/castle_v34.yaml')
(root/'firmware/build_path.yaml').write_text('esphome:\n  build_path: '+str(root/'build')+'\n', encoding='utf-8')
PY
"${ESPHOME_CLI:-$source_repo/.venv/bin/esphome}" compile "$build_root/firmware/castle_v34.yaml" 2>&1 | tee "$build_root/build.log"
python3 firmware-reference/record_build.py "$build_root" "$source_repo"
