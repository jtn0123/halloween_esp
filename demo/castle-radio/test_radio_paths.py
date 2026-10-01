"""CASTLE_RADIO_DATA moves the whole library out of the checkout — the
desktop app's per-user data dir — and unset it changes nothing."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import library_ops
import radio_paths

HERE = Path(__file__).resolve().parent


class DataDirTest(unittest.TestCase):
    def test_default_is_beside_the_server(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CASTLE_RADIO_DATA", None)
            self.assertEqual(radio_paths.data_dir(), HERE / ".radio-data")

    def test_variable_moves_it(self):
        with mock.patch.dict(os.environ, {"CASTLE_RADIO_DATA": "/x/radio"}):
            self.assertEqual(radio_paths.data_dir(), Path("/x/radio"))

    def test_server_library_follows_the_variable(self):
        """A fresh interpreter, as the app starts it: radio_jobs creates the
        library under the variable and points the toolchain there too."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "nested" / "radio"
            env = {**os.environ, "CASTLE_RADIO_DATA": str(data)}
            probe = (
                "import json, os, radio_jobs; print(json.dumps([str(radio_jobs.DATA), "
                "os.environ['CASTLE_TRACKS'], os.environ['CASTLE_SCENES']]))"
            )
            out = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=HERE,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            got = json.loads(out.stdout.strip().splitlines()[-1])
            self.assertEqual(
                got, [str(data), str(data / "tracks"), str(data / "scenes.yaml")]
            )
            self.assertTrue((data / "tracks").is_dir())

    def test_waveform_reads_the_given_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, data = Path(tmp) / "root", Path(tmp) / "data"
            library = data / "tracks"
            (library / "stems" / "radio_x").mkdir(parents=True)
            (library / "stems" / "radio_x" / "analysis.json").write_text('{"ok": 1}')
            data.mkdir(exist_ok=True)
            (data / "catalog.json").write_text('[{"key": "radio_x"}]')
            self.assertEqual(
                library_ops.waveform(root, library, "radio_x", data), {"ok": 1}
            )
            with self.assertRaises(ValueError):
                library_ops.waveform(root, library, "radio_x")


if __name__ == "__main__":
    unittest.main()
