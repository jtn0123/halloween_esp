"""The published PCB handoff must work without KiCad's ignored local settings."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
HANDOFF = ROOT / "hardware/castle-carrier-v3.4/integrated"


class TestPublishedManifest(unittest.TestCase):
    def test_manifest_does_not_require_local_settings(self) -> None:
        manifest = json.loads(
            (HANDOFF / "qa/import-manifest.json").read_text(encoding="utf-8")
        )
        self.assertFalse(any(name.endswith(".kicad_prl") for name in manifest["files"]))
        for name in manifest["files"]:
            self.assertTrue((HANDOFF / name).is_file(), name)


class TestIntegrity(unittest.TestCase):
    def setUp(self) -> None:
        import shutil
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "handoff"
        shutil.copytree(
            HANDOFF, self.root, ignore=shutil.ignore_patterns("local-checks")
        )

    def test_clean_handoff_and_optional_local_settings(self) -> None:
        from check_pcb_handoff import check

        check(self.root)
        (self.root / "castle-carrier.kicad_prl").write_text(
            "local-only", encoding="utf-8"
        )
        check(self.root)

    def test_missing_published_file_has_actionable_error(self) -> None:
        from check_pcb_handoff import check

        (self.root / "castle-carrier.kicad_sch").unlink()
        with self.assertRaisesRegex(ValueError, "Missing published handoff file"):
            check(self.root)

    def test_native_tampering_is_rejected(self) -> None:
        from check_pcb_handoff import check

        with (self.root / "castle-carrier.kicad_pcb").open("ab") as f:
            f.write(b"tamper")
        with self.assertRaisesRegex(ValueError, "Handoff hash mismatch"):
            check(self.root)

    def test_package_native_mismatch_even_with_updated_import_hash(self) -> None:
        import hashlib

        from check_pcb_handoff import check

        pcb = self.root / "castle-carrier.kicad_pcb"
        pcb.write_bytes(pcb.read_bytes() + b"tamper")
        path = self.root / "qa/import-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["files"][pcb.name] = hashlib.sha256(pcb.read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError, "Manufacturing package does not match native PCB"
        ):
            check(self.root)

    def test_package_tampering_is_rejected(self) -> None:
        from check_pcb_handoff import check

        path = self.root / "out/DFM_REVIEW_NOT_RELEASED.zip"
        path.write_bytes(b"not the reviewed package")
        with self.assertRaisesRegex(ValueError, "Handoff hash mismatch"):
            check(self.root)

    def test_unsafe_manifest_path_is_rejected(self) -> None:
        from check_pcb_handoff import check

        path = self.root / "qa/import-manifest.json"
        path.write_text(json.dumps({"files": {"../outside": "0"}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid handoff path"):
            check(self.root)

    def test_regeneration_uses_published_files_not_local_settings(self) -> None:
        import subprocess

        from check_pcb_handoff import check, refresh_manifest

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        (self.root / "castle-carrier.kicad_prl").write_text(
            "ignored state", encoding="utf-8"
        )
        (self.root / "README.md").write_text("updated review notes", encoding="utf-8")
        refresh_manifest(self.root)
        manifest = json.loads(
            (self.root / "qa/import-manifest.json").read_text(encoding="utf-8")
        )
        self.assertIn("castle-carrier.kicad_pcb", manifest["files"])
        self.assertNotIn("castle-carrier.kicad_prl", manifest["files"])
        self.assertNotIn("qa/import-manifest.json", manifest["files"])
        check(self.root)

    def test_bom_mismatch_even_with_updated_import_hash(self) -> None:
        import hashlib

        from check_pcb_handoff import check

        bom = self.root / "bom-v3.4-engineering.csv"
        bom.write_bytes(bom.read_bytes() + b"wrong part\n")
        path = self.root / "qa/import-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["files"][bom.name] = hashlib.sha256(bom.read_bytes()).hexdigest()
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError, "Manufacturing BOM does not match engineering BOM"
        ):
            check(self.root)

    def rewrite_review_packet(self, change: str) -> None:
        import hashlib
        import zipfile

        package = self.root / "out/DFM_REVIEW_NOT_RELEASED.zip"
        with zipfile.ZipFile(package) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        record_name = "dfm-review/package-manifest.json"
        record = json.loads(contents[record_name])
        if change == "finish":
            name = "gerbers/castle-carrier-job.gbrjob"
            job = json.loads(contents["dfm-review/" + name])
            job["GeneralSpecs"]["Finish"] = "HASL"
            contents["dfm-review/" + name] = json.dumps(job).encode()
        else:
            name = "filled-capped-holes.csv"
            lines = contents["dfm-review/" + name].splitlines(keepends=True)
            contents["dfm-review/" + name] = b"".join(lines[:-1])
        record["files"][name] = hashlib.sha256(
            contents["dfm-review/" + name]
        ).hexdigest()
        contents[record_name] = json.dumps(record).encode()
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in contents.items():
                archive.writestr(name, data)
        manifest_path = self.root / "qa/import-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["out/DFM_REVIEW_NOT_RELEASED.zip"] = hashlib.sha256(
            package.read_bytes()
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_wrong_finish_is_rejected_even_when_package_hashes_agree(self) -> None:
        from check_pcb_handoff import check

        self.rewrite_review_packet("finish")
        with self.assertRaisesRegex(ValueError, "ENIG mismatch"):
            check(self.root)

    def test_missing_thermal_hole_is_rejected_even_when_package_hashes_agree(
        self,
    ) -> None:
        from check_pcb_handoff import check

        self.rewrite_review_packet("hole")
        with self.assertRaisesRegex(ValueError, "exactly 16"):
            check(self.root)

    def test_cli_reports_pass_and_missing_file_as_nonzero(self) -> None:
        import contextlib
        import io
        from unittest.mock import patch

        from check_pcb_handoff import main

        with patch.object(
            sys, "argv", ["check_pcb_handoff.py", "--root", str(self.root)]
        ):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 0)
            self.assertIn("PASS", output.getvalue())
            (self.root / "castle-carrier.kicad_sch").unlink()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 1)
            self.assertIn("Missing published", output.getvalue())
