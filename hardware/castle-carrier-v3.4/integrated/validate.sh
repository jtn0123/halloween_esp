#!/usr/bin/env bash
# Never save a refilled board over the reviewed native PCB.
set -euo pipefail
cd "$(dirname "$0")"

python3 - <<'PY'
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

def digest(data):
    return hashlib.sha256(data).hexdigest()

manifest = json.loads(Path('qa/import-manifest.json').read_text())
for name, expected in manifest['files'].items():
    if digest(Path(name).read_bytes()) != expected:
        raise SystemExit('Handoff hash mismatch: ' + name)
pcb = Path('castle-carrier.kicad_pcb').read_bytes()
with zipfile.ZipFile('out/DFM_REVIEW_NOT_RELEASED.zip') as package:
    record = json.loads(package.read('dfm-review/package-manifest.json'))
    if record['native_pcb_sha256'] != digest(pcb):
        raise SystemExit('Manufacturing package does not match native PCB')
    for name, expected in record['files'].items():
        if digest(package.read("dfm-review/" + name)) != expected:
            raise SystemExit('Manufacturing package hash mismatch: ' + name)
    job = json.loads(package.read('dfm-review/gerbers/castle-carrier-job.gbrjob'))
    if job['GeneralSpecs']['Finish'] != 'ENIG' or b'(copper_finish "ENIG")' not in pcb:
        raise SystemExit('Native/export ENIG mismatch')
    holes = list(csv.DictReader(io.StringIO(package.read('dfm-review/filled-capped-holes.csv').decode())))
    if len(holes) != 16 or record['filled_holes'] != 16:
        raise SystemExit('Expected exactly 16 filled/capped thermal holes')
print('Handoff/package integrity: PASS; ENIG; 16 thermal holes')
PY

if [ -z "${KICAD_CLI:-}" ]; then
  if command -v kicad-cli >/dev/null 2>&1; then
    KICAD_CLI=$(command -v kicad-cli)
  else
    KICAD_CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
  fi
fi
if [ ! -x "$KICAD_CLI" ]; then
  echo 'Install KiCad 10 and set KICAD_CLI to its executable.' >&2
  exit 1
fi
kicad_support="$(dirname "$KICAD_CLI")/../SharedSupport"
if [ -d "$kicad_support/footprints" ]; then
  export KICAD10_FOOTPRINT_DIR="$kicad_support/footprints"
  export KICAD10_SYMBOL_DIR="$kicad_support/symbols"
  export KICAD10_3DMODEL_DIR="$kicad_support/3dmodels"
fi
mkdir -p local-checks
"$KICAD_CLI" pcb drc --refill-zones --schematic-parity --severity-all \
  --exit-code-violations --format json -o local-checks/drc.json castle-carrier.kicad_pcb
"$KICAD_CLI" sch erc --severity-all --exit-code-violations --format json \
  -o local-checks/erc.json castle-carrier.kicad_sch
echo 'Fresh handoff CAD checks: PASS. Physical validation: NOT PERFORMED.'
