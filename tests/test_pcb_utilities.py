"""Offline handoff controls supplement, and do not replace, native/bench QA."""

from __future__ import annotations

import array
import contextlib
import hashlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "hardware/castle-carrier-v3.4/integrated"


def helper(relative: str) -> dict:
    return runpy.run_path(str(BOARD / relative))


class TestBuildReceipt(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name) / "integrated"
        self.workspace = self.board / "local-checks/firmware"
        self.build = self.workspace / "build"
        self.module = helper("firmware-reference/record_build.py")
        self.cpp = "\n".join(
            [
                "set_slot_mode(I2S_SLOT_MODE_STEREO)",
                "set_std_slot_mask(I2S_STD_SLOT_BOTH)",
                "set_announcement_format({.num_channels = 2});",
                "castle_speaker->set_dout_pin(15)",
                "i2s_bus->set_bclk_pin(11)",
                "i2s_bus->set_lrclk_pin(12)",
                "castle_rate_safe_speaker->set_target_sample_rate(44100)",
                "castle_rate_safe_speaker->set_target_bits_per_sample(16)",
                "castle_rate_safe_speaker->set_buffer_duration(100)",
                "castle_speaker->set_slot_bit_width(I2S_SLOT_BIT_WIDTH_16BIT)",
                "castle_speaker->set_sample_rate(44100)",
                "castle_rate_safe_speaker->set_output_speaker(castle_speaker)",
                "castle_media->set_announcement_speaker(castle_rate_safe_speaker)",
                'App.pre_setup("castle-v34-integrated"',
            ]
        )
        files = {
            self.build / "src/main.cpp": self.cpp,
            self.build / "src/esphome/core/defines.h": "\n".join(
                "#define USE_AUDIO_" + codec + "_SUPPORT"
                for codec in ["MP3", "WAV", "OPUS"]
            ),
            self.build
            / "sdkconfig.castle-v34-integrated": "CONFIG_SPIRAM_MODE_OCT=y\nCONFIG_ESPTOOLPY_FLASHSIZE_16MB=y",
            self.board
            / "firmware-reference/castle_v34.yaml": 'channel: stereo\nnum_channels: 2\n  version: "5.78"\n',
            self.workspace
            / "build.log": "INFO ESPHome 2026.9.1\nChecking ESP-IDF 5.5.5 framework\nRAM: 36.2%\nFlash: 15.9%\nSuccessfully compiled program.\n",
            self.workspace / "firmware/castle.yaml": "dummy source",
            self.workspace / "firmware/secrets.yaml": "dummy excluded credentials",
            self.workspace / "firmware/.esphome/generated.cpp": "excluded cache",
        }
        for path, contents in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
        for name in ["firmware.elf", "firmware.ota.bin", "firmware.factory.bin"]:
            (self.build / name).write_bytes(name.encode())

    def receipt(self) -> dict:
        return cast(
            dict,
            self.module["build_receipt"](self.board, self.workspace, "fixture-commit"),
        )

    def test_stereo_receipt_binds_artifacts_and_excludes_credentials(self) -> None:
        report = self.receipt()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["firmware_version"], "5.78")
        self.assertEqual(report["idf_version"], "5.5.5")
        self.assertEqual(
            report["firmware_source_files_sha256"],
            {"castle.yaml": hashlib.sha256(b"dummy source").hexdigest()},
        )
        self.assertEqual(
            report["artifacts"]["firmware.ota.bin"],
            hashlib.sha256(b"firmware.ota.bin").hexdigest(),
        )
        self.assertIn("no flashing", report["scope"])

    def test_mono_wrong_pin_rate_codec_and_memory_are_rejected(self) -> None:
        changes = [
            ("src/main.cpp", "I2S_SLOT_MODE_STEREO", "I2S_SLOT_MODE_MONO", "slot mode"),
            ("src/main.cpp", "I2S_STD_SLOT_BOTH", "I2S_STD_SLOT_LEFT", "Both stereo"),
            ("src/main.cpp", ".num_channels = 2", ".num_channels = 1", "not stereo"),
            ("src/main.cpp", "set_dout_pin(15)", "set_dout_pin(16)", "Wrong audio pin"),
            (
                "src/main.cpp",
                "set_target_sample_rate(44100)",
                "set_target_sample_rate(48000)",
                "audio/device setting",
            ),
            (
                "src/esphome/core/defines.h",
                "USE_AUDIO_OPUS_SUPPORT",
                "REMOVED_CODEC",
                "Missing codec",
            ),
            (
                "sdkconfig.castle-v34-integrated",
                "CONFIG_SPIRAM_MODE_OCT=y",
                "CONFIG_SPIRAM_MODE_QUAD=y",
                "flash/PSRAM",
            ),
            (
                "sdkconfig.castle-v34-integrated",
                "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y",
                "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y",
                "flash/PSRAM",
            ),
        ]
        changes.extend(
            [
                (
                    "src/main.cpp",
                    "set_target_bits_per_sample(16)",
                    "set_target_bits_per_sample(32)",
                    "audio/device setting",
                ),
                (
                    "src/main.cpp",
                    "set_buffer_duration(100)",
                    "set_buffer_duration(50)",
                    "audio/device setting",
                ),
                (
                    "src/main.cpp",
                    "I2S_SLOT_BIT_WIDTH_16BIT",
                    "I2S_SLOT_BIT_WIDTH_32BIT",
                    "slot bit width",
                ),
                (
                    "src/main.cpp",
                    "castle_speaker->set_sample_rate(44100)",
                    "castle_speaker->set_sample_rate(48000)",
                    "audio/device setting",
                ),
            ]
        )
        for relative, before, after, error in changes:
            with self.subTest(relative=relative, change=after):
                path = self.build / relative
                original = path.read_text(encoding="utf-8")
                path.write_text(original.replace(before, after), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error):
                    self.receipt()
                path.write_text(original, encoding="utf-8")

    def test_failed_compile_missing_metadata_and_duplicate_artifact_are_rejected(
        self,
    ) -> None:
        log = self.workspace / "build.log"
        original = log.read_text(encoding="utf-8")
        for contents, error in [
            ("[FAILED]", "did not succeed"),
            ("[SUCCESS]", "Missing build metadata"),
        ]:
            log.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, error):
                self.receipt()
        log.write_text(original, encoding="utf-8")
        duplicate = self.build / "duplicate/firmware.elf"
        duplicate.parent.mkdir()
        duplicate.write_bytes(b"duplicate")
        with self.assertRaisesRegex(ValueError, "exactly one artifact"):
            self.receipt()

    def test_cli_receipt_output_is_fixed_under_the_handoff(self) -> None:
        main = self.module["main"]
        main.__globals__["__file__"] = str(
            self.board / "firmware-reference/record_build.py"
        )
        for args in [
            ["record_build.py"],
            ["record_build.py", str(self.board.parent / "read-only-source")],
        ]:
            with (
                patch.object(sys, "argv", args),
                patch("subprocess.check_output", return_value="fixture-commit\n"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                main()
            output = self.workspace / "firmware-build-check.json"
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["status"], "PASS"
            )
            self.assertFalse(
                (
                    self.board.parent / "read-only-source/firmware-build-check.json"
                ).exists()
            )
        with patch.object(
            sys, "argv", ["record_build.py", "source", "unexpected-output"]
        ):
            with self.assertRaisesRegex(ValueError, "at most one"):
                main()

    def test_optimized_python_still_rejects_a_mono_build(self) -> None:
        (self.build / "src/main.cpp").write_text(
            self.cpp.replace("I2S_SLOT_MODE_STEREO", "I2S_SLOT_MODE_MONO"),
            encoding="utf-8",
        )
        code = (
            "import runpy,sys; from pathlib import Path; "
            "m=runpy.run_path(sys.argv[1]); "
            "m['build_receipt'](Path(sys.argv[2]),Path(sys.argv[3]),'fixture')"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-O",
                "-c",
                code,
                str(BOARD / "firmware-reference/record_build.py"),
                str(self.board),
                str(self.workspace),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Stereo slot mode missing", result.stderr)


class TestStereoFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.module = helper("stereo-bench/generate.py")

    def test_encoded_decoded_mp3_preserves_left_right_and_identical_channels(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.module["generate"](Path(directory))
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual(report["physical_playback"], "NOT_PERFORMED")
            self.assertEqual(report["audio"]["channels"], 2)

    def test_silent_short_swapped_and_mono_fixtures_cannot_pass(self) -> None:
        rate = self.module["RATE"]
        check = self.module["check_channels"]
        short = array.array("h", [0, 0])
        with self.assertRaisesRegex(ValueError, "too short"):
            check(short)
        checks, _ = check(array.array("h", [0]) * (rate * 9 * 2))
        self.assertFalse(any(checks.values()))
        data = self.module["samples"]()
        for i in range(0, len(data), 2):
            data[i], data[i + 1] = data[i + 1], data[i]
        checks, _ = check(data)
        self.assertFalse(checks["left_only"])
        self.assertFalse(checks["right_only"])
        self.assertTrue(checks["same_both"])
        for i in range(0, len(data), 2):
            data[i + 1] = data[i]
        checks, _ = check(data)
        self.assertFalse(checks["left_only"])
        self.assertFalse(checks["right_only"])


class FixturePad:
    def __init__(self, row: dict, parent: "FixtureFootprint") -> None:
        self.row, self.parent = row, parent

    def GetNetname(self) -> str:
        return str(self.row["net"])

    def GetNumber(self) -> str:
        return str(self.row["number"])

    def GetPosition(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            x=self.row["position"][0], y=self.row["position"][1]
        )

    def HitTest(self, point: tuple) -> bool:
        return list(point) in self.row["hit_points"]

    def GetParentFootprint(self) -> "FixtureFootprint":
        return self.parent


class FixtureFootprint:
    def __init__(self, reference: str, rows: list) -> None:
        self.reference = reference
        self.pads = [FixturePad(row, self) for row in rows]

    def GetReference(self) -> str:
        return self.reference

    def Pads(self) -> list:
        return self.pads


class FixtureTrack:
    def __init__(self, row: dict) -> None:
        self.row = row

    def GetNetname(self) -> str:
        return str(self.row["net"])

    def GetStart(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(x=self.row["start"][0], y=self.row["start"][1])

    def GetEnd(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(x=self.row["end"][0], y=self.row["end"][1])

    def GetLength(self) -> float:
        return float(self.row["length"])


class CheckerExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


def checker_exit(status: int) -> None:
    raise CheckerExit(status)


class TestUsbAlgorithm(unittest.TestCase):
    def evaluate(self, row: dict) -> tuple[int, dict]:
        footprints = [
            FixtureFootprint(reference, pads)
            for reference, pads in row["footprints"].items()
        ]
        board = types.SimpleNamespace(
            GetFootprints=lambda: footprints,
            GetTracks=lambda: [FixtureTrack(track) for track in row["tracks"]],
        )
        pcbnew = types.SimpleNamespace(
            LoadBoard=lambda _: board,
            PCB_VIA=type("Via", (), {}),
            VECTOR2I=lambda x, y: (x, y),
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(sys.modules, {"pcbnew": pcbnew}),
                patch.object(sys, "argv", ["check_usb_routes.py"]),
                patch.object(Path, "cwd", return_value=Path(directory)),
                patch("os._exit", side_effect=checker_exit),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(CheckerExit) as stopped:
                    runpy.run_path(str(BOARD / "qa/check_usb_routes.py"))
            report = json.loads(
                (Path(directory) / "out/usb-route-check.json").read_text(
                    encoding="utf-8"
                )
            )
        return stopped.exception.status, report

    def test_native_before_geometry_fails_and_current_geometry_passes(self) -> None:
        fixtures = json.loads(
            (ROOT / "tests/fixtures/pcb_usb_routes.json").read_text(encoding="utf-8")
        )
        before_status, before = self.evaluate(fixtures["before"])
        current_status, current = self.evaluate(fixtures["current"])
        self.assertEqual(before_status, 1)
        self.assertEqual(before["status"], "FAIL")
        self.assertAlmostEqual(
            before["orientation_pair_mismatch_mm"]["A_contacts"], 2.308741528, places=5
        )
        self.assertEqual(current_status, 0)
        for mismatch in current["orientation_pair_mismatch_mm"].values():
            self.assertAlmostEqual(mismatch, 0.795, places=5)
        self.assertEqual(
            fixtures["current"]["native_pcb_sha256"],
            hashlib.sha256(
                (BOARD / "castle-carrier.kicad_pcb").read_bytes()
            ).hexdigest(),
        )

    def test_disconnected_paths_are_rejected(self) -> None:
        fixtures = json.loads(
            (ROOT / "tests/fixtures/pcb_usb_routes.json").read_text(encoding="utf-8")
        )
        row = fixtures["current"]
        row["tracks"] = []
        with self.assertRaisesRegex(AssertionError, "Disconnected USB path"):
            self.evaluate(row)
