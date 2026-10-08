#!/usr/bin/env bash
# Never save a refilled board over the reviewed native PCB.
set -euo pipefail
cd "$(dirname "$0")"

python3 ../../../tools/check_pcb_handoff.py --root "$PWD"

if [[ -z "${KICAD_CLI:-}" ]]; then
  if command -v kicad-cli >/dev/null 2>&1; then
    KICAD_CLI=$(command -v kicad-cli)
  else
    KICAD_CLI=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
  fi
fi
if [[ ! -x "$KICAD_CLI" ]]; then
  echo 'Install KiCad 10 and set KICAD_CLI to its executable.' >&2
  exit 1
fi
kicad_support="$(dirname "$KICAD_CLI")/../SharedSupport"
if [[ -d "$kicad_support/footprints" ]]; then
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
