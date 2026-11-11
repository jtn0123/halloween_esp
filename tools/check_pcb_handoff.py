"""Verify a published PCB handoff; local KiCad settings are never release inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "hardware/castle-carrier-v3.4/integrated"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_file(root: Path, name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Invalid handoff path: " + name)
    return root / path


def check_import_manifest(root: Path) -> None:
    manifest = json.loads(
        (root / "qa/import-manifest.json").read_text(encoding="utf-8")
    )
    for name, expected in manifest["files"].items():
        if name.endswith(".kicad_prl"):
            raise ValueError("Manifest includes local KiCad settings: " + name)
        path = relative_file(root, name)
        if not path.is_file():
            raise ValueError("Missing published handoff file: " + name)
        if digest(path.read_bytes()) != expected:
            raise ValueError("Handoff hash mismatch: " + name)


def check(root: Path) -> None:
    check_import_manifest(root)
    pcb = (root / "castle-carrier.kicad_pcb").read_bytes()
    with zipfile.ZipFile(root / "out/DFM_REVIEW_NOT_RELEASED.zip") as package:
        record = json.loads(package.read("dfm-review/package-manifest.json"))
        if record["native_pcb_sha256"] != digest(pcb):
            raise ValueError("Manufacturing package does not match native PCB")
        for name, expected in record["files"].items():
            relative_file(root, name)
            if digest(package.read("dfm-review/" + name)) != expected:
                raise ValueError("Manufacturing package hash mismatch: " + name)
        if (
            package.read("dfm-review/engineering-bom.csv")
            != (root / "bom-v3.4-engineering.csv").read_bytes()
        ):
            raise ValueError("Manufacturing BOM does not match engineering BOM")
        job = json.loads(package.read("dfm-review/gerbers/castle-carrier-job.gbrjob"))
        if (
            job["GeneralSpecs"]["Finish"] != "ENIG"
            or b'(copper_finish "ENIG")' not in pcb
        ):
            raise ValueError("Native/export ENIG mismatch")
        holes = list(
            csv.DictReader(
                io.StringIO(package.read("dfm-review/filled-capped-holes.csv").decode())
            )
        )
        if len(holes) != 16 or record["filled_holes"] != 16:
            raise ValueError("Expected exactly 16 filled/capped thermal holes")


def refresh_manifest(root: Path) -> None:
    """Hash git's published file set, never an unrestricted workspace scan."""
    root = root.resolve()
    repo = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    prefix = root.resolve().relative_to(repo.resolve())
    tracked = (
        subprocess.run(
            ["git", "ls-files", "-z", "--", str(prefix)],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    manifest_path = root / "qa/import-manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {}
    for name in sorted(filter(None, tracked)):
        path = repo / name
        if path == manifest_path or path.name.endswith(".kicad_prl"):
            continue
        files[str(path.relative_to(root))] = digest(path.read_bytes())
    manifest["files"] = files
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT)
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    try:
        if args.write_manifest:
            refresh_manifest(args.root.resolve())
        check(args.root)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        print("Handoff rejected:", exc)
        return 1
    print("Handoff/package integrity: PASS; ENIG; 16 thermal holes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
