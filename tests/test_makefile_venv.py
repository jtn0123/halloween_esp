"""`make` without a venv must fail fast, except setup/help and a PY= override."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class MakefileVenvTests(unittest.TestCase):
    def test_missing_venv_tells_you_to_run_setup(self):
        text = (ROOT / "Makefile").read_text()
        self.assertIn("run make setup", text)
        self.assertIn("origin PY),command line", text)
        self.assertNotIn("|| echo python3", text)


if __name__ == "__main__":
    unittest.main()
