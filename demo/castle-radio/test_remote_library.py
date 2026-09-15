import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import remote_library


class FakeResponse:
    status = 200

    def __init__(self, data, crc=None):
        self.data = data
        self.crc = zlib.crc32(data) if crc is None else crc

    def read(self):
        return json.dumps(
            {"bytes": len(self.data), "crc32": f"{self.crc:08x}"}
        ).encode()


class FakeConnection:
    def __init__(self, data, crc=None):
        self.data = data
        self.crc = crc
        self.sent = bytearray()

    def putrequest(self, *_args):
        pass

    def putheader(self, *_args):
        pass

    def endheaders(self):
        pass

    def send(self, block):
        self.sent.extend(block)

    def getresponse(self):
        return FakeResponse(self.data, self.crc)

    def close(self):
        pass


class RemoteLibraryTests(unittest.TestCase):
    @patch("device_bridge.call")
    def test_audio_only_is_not_complete_show(self, call):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media").mkdir()
            (root / "media" / "01_vigil.mp3").write_bytes(b"local")
            (root / "radio_a.mp3").write_bytes(b"audio")
            call.side_effect = [
                {"scenes": "vigil"},
                [{"name": "radio_a.mp3", "size": 5}],
                [{"name": "01_vigil.mp3", "size": 99}],
            ]
            result = remote_library.inventory(root, root, [{"key": "radio_a"}])[
                "tracks"
            ]
            self.assertEqual(result["01_vigil.mp3"]["status"], "ready")
            self.assertEqual(result["radio_a"]["status"], "audio_only")
            self.assertFalse(result["radio_a"]["lights"])
            self.assertEqual(result["radio_a"]["filename"], "radio_a.mp3")
            self.assertEqual(result["radio_a"]["bytes"], 5)

    @patch("device_bridge.call")
    def test_missing_scene_audio_is_not_ready(self, call):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media").mkdir()
            (root / "media" / "01_vigil.mp3").touch()
            call.side_effect = [{"scenes": "vigil"}, [], []]
            result = remote_library.inventory(root, root, [])["tracks"]["01_vigil.mp3"]
            self.assertEqual(result["status"], "missing")
            self.assertTrue(result["can_sync"])

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            remote_library.start(Path("/tmp"), Path("/tmp"), [], "../private.mp3")

    def test_catalog_playback_filename_selects_opus(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)
            opus = library / "radio_a.opus"
            opus.write_bytes(b"opus")
            row = {"key": "radio_a", "playback_file": opus.name}
            self.assertEqual(remote_library.playback_path(library, row), opus)

    @patch("device_bridge.call")
    def test_other_audio_includes_size_and_can_be_deleted(self, call):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media").mkdir()
            call.side_effect = [
                {"scenes": ""},
                [{"name": "orphan.mp3", "size": 2048}],
                [],
                {"deleted": True},
            ]
            other = remote_library.inventory(root, root, [])["other_audio"]
            self.assertEqual(other, [{"name": "orphan.mp3", "bytes": 2048}])
            self.assertEqual(
                remote_library.delete_audio("orphan.mp3"), {"deleted": True}
            )
            self.assertEqual(call.call_args.args, ("/api/files/orphan.mp3", "DELETE"))

    @patch("device_bridge.call")
    def test_delete_rejects_non_audio_and_paths(self, call):
        for name in ("../private.mp3", "site.html", ""):
            with self.assertRaises(ValueError):
                remote_library.delete_audio(name)
        call.assert_not_called()

    def test_chunked_upload_reports_real_progress(self):
        data = b"a" * 70000
        connection = FakeConnection(data)
        remote_library._JOBS["test"] = {"done": False}
        remote_library.upload_with_progress(
            "test", "/api/files", "test.mp3", data, lambda: connection
        )
        self.assertEqual(connection.sent, data)
        self.assertEqual(remote_library._JOBS["test"]["sent_bytes"], len(data))
        self.assertEqual(remote_library._JOBS["test"]["percent"], 99)
        self.assertEqual(
            remote_library._JOBS["test"]["phase"], "Verifying castle SD copy"
        )
        del remote_library._JOBS["test"]

    def test_crc_failure_is_reported(self):
        connection = FakeConnection(b"audio", crc=0)
        remote_library._JOBS["test"] = {"done": False}
        with self.assertRaisesRegex(OSError, "CRC mismatch"):
            remote_library.upload_with_progress(
                "test", "/api/files", "test.mp3", b"audio", lambda: connection
            )
        del remote_library._JOBS["test"]
