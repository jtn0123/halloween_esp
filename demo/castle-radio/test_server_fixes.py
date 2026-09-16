"""Small pins for the radio HTTP server's request parsing."""

import inspect
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import server


class ServerFixTests(unittest.TestCase):
    def test_sync_status_reads_key_as_a_query_param(self):
        self.assertEqual((parse_qs("x=1&key=job").get("key") or [""])[0], "job")
        self.assertIn("parse_qs", inspect.getsource(server.Handler.get_sync_status))

    def test_retry_and_reprocess_use_the_json_body_cap(self):
        self.assertIn("json_body", inspect.getsource(server.Handler.post_retry))
        self.assertIn("json_body", inspect.getsource(server.Handler.post_reprocess))
        self.assertNotIn("upload_length", inspect.getsource(server.Handler.post_retry))

    def test_imported_show_reads_the_catalog_under_lock(self):
        src = inspect.getsource(server.imported_show)
        self.assertIn("LOCK", src)
        with patch("server.catalog", return_value=[{"key": "radio_a", "cues": [1]}]):
            self.assertEqual(
                server.imported_show({"action": "file", "key": "radio_a"})["cues"], [1]
            )


if __name__ == "__main__":
    unittest.main()
