"""Uninstalling the desktop app leaves the owner's songs unless asked.

docs/PRODUCTION-TODO.md §5.4. The app keeps tracks, scenes and settings in
Tauri's app-data directory for its identifier (desktop/README.md "Removing
the app keeps the songs"), and what deletes that directory on Windows is the
NSIS uninstaller. Ours is Tauri's stock one, whose behaviour was read from
the template of the bundler the pinned CLI ships (tauri-bundler 2.10.1 under
@tauri-apps/cli 2.12.1, src/bundle/windows/nsis/installer.nsi):

  * the uninstall confirm page adds a "Delete the application data" box,
    unticked when it is created;
  * `RmDir /r "$APPDATA\\${BUNDLEID}"` and the `$LOCALAPPDATA` twin run only
    when that box was ticked AND the run is not an update (`/UPDATE`); a
    silent or passive run skips the page, so the box is never ticked;
  * the install folder is `$LOCALAPPDATA\\${PRODUCTNAME}` and is removed
    file by file, then with a non-recursive `RMDir` — the data is elsewhere.

None of that is in this repo, so what is tested is that the configuration
leaves the template alone: no custom template, no uninstall hooks, NSIS
only, per-user, the identifier the data directory is named after, and the
CLI pinned to the version whose template was read. Bumping the CLI fails
here on purpose: read the new template's uninstall section, then move the pin.
macOS has no uninstaller to configure (desktop/README.md says what dragging
the app to the Bin keeps).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "desktop" / "src-tauri"
CLI = ROOT / "desktop" / "cli" / "package.json"

#: The CLI whose bundler's NSIS template the docstring describes.
AUDITED_CLI = "2.12.1"
IDENTIFIER = "io.github.jtn0123.castletools"


def conf(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((TAURI / name).read_text(encoding="utf-8"))
    return loaded


class UninstallKeepsTheSongs(unittest.TestCase):
    def test_windows_ships_the_nsis_installer_alone(self) -> None:
        self.assertEqual(conf("tauri.windows.conf.json")["bundle"]["targets"], ["nsis"])

    def test_per_user_with_the_stock_template_and_no_hooks(self) -> None:
        nsis = conf("tauri.conf.json")["bundle"]["windows"]["nsis"]
        self.assertEqual(nsis["installMode"], "currentUser")
        for name in sorted(TAURI.glob("tauri*.conf.json")):
            with self.subTest(name.name):
                windows = conf(name.name).get("bundle", {}).get("windows", {})
                for key in ("template", "installerHooks"):
                    self.assertNotIn(key, windows.get("nsis", {}))
                self.assertNotIn(
                    "wix", windows, "an MSI's uninstaller is not the audited one"
                )

    def test_the_data_directory_is_named_after_this_identifier(self) -> None:
        self.assertEqual(conf("tauri.conf.json")["identifier"], IDENTIFIER)
        readme = (ROOT / "desktop" / "README.md").read_text(encoding="utf-8")
        for path in (
            f"%APPDATA%\\{IDENTIFIER}",
            f"~/Library/Application Support/{IDENTIFIER}",
        ):
            self.assertIn(path, readme)

    def test_the_cli_is_the_one_whose_template_was_read(self) -> None:
        pin = json.loads(CLI.read_text(encoding="utf-8"))["devDependencies"]
        self.assertEqual(
            pin["@tauri-apps/cli"],
            AUDITED_CLI,
            "a new Tauri CLI brings a new NSIS template: re-read its uninstall "
            "section (the delete-app-data box and its RmDir) before moving "
            "AUDITED_CLI",
        )


if __name__ == "__main__":
    unittest.main()
