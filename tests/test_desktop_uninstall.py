"""Uninstalling the desktop app removes its tools and keeps the songs.

docs/PRODUCTION-TODO.md §5.4. The app keeps tracks, scenes and settings in
Tauri's app-data directory for its identifier, and the ~1.7 GB its first
launch fetched in the local one's `runtime/` (desktop/README.md "Removing
the app"). What deletes either on Windows is the NSIS uninstaller: Tauri's
stock one, whose behaviour was read from the template of the bundler the
pinned CLI ships (tauri-bundler 2.10.1 under @tauri-apps/cli 2.12.1,
src/bundle/windows/nsis/installer.nsi):

  * the uninstall confirm page adds a "Delete the application data" box,
    unticked when it is created;
  * `RmDir /r "$APPDATA\\${BUNDLEID}"` and the `$LOCALAPPDATA` twin run only
    when that box was ticked AND the run is not an update (`/UPDATE`); a
    silent or passive run skips the page, so the box is never ticked;
  * the install folder is `$LOCALAPPDATA\\${PRODUCTNAME}` and is removed
    file by file, then with a non-recursive `RMDir` — the data is elsewhere;
  * `installerHooks` is `!include`d, and NSIS_HOOK_POSTUNINSTALL runs last in
    `Section Uninstall`, after CheckIfAppIsRunning, where `$UpdateMode`,
    `$EXEDIR`, `$INSTDIR` and `${BUNDLEID}` are all set;
  * a newer setup's "Uninstall before installing" runs uninstall.exe with
    `_?=` (in place, never as an update), while an uninstaller started any
    other way copies itself to %TEMP% first.

So the configuration is the stock template plus one hook file, and this
holds the hook to removing the runtime and nothing of the owner's: no
`$APPDATA`, no songs, no settings, no log, and not on an update or an
upgrade's uninstall. The runtime's junction to the songs goes first and
alone, because `RMDir /r` follows one into its target. tools/desktop_smoke.py runs both uninstalls against the
real build on Windows. Bumping the CLI fails here on purpose: read the new
template's uninstall section, then move the pin. macOS has no uninstaller to
configure; docs/OWNER-GUIDE.md says which folder to delete.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_env as de

TAURI = ROOT / "desktop" / "src-tauri"
CLI = ROOT / "desktop" / "cli" / "package.json"
HOOKS = "./windows/hooks.nsh"

#: The CLI whose bundler's NSIS template the docstring describes.
AUDITED_CLI = "2.12.1"
IDENTIFIER = "io.github.jtn0123.castletools"


def conf(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((TAURI / name).read_text(encoding="utf-8"))
    return loaded


def hook_lines() -> list[str]:
    """The hook file's NSIS, without its comments."""
    text = (TAURI / HOOKS).read_text(encoding="utf-8")
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith(";")
    ]


class UninstallKeepsTheSongs(unittest.TestCase):
    def test_windows_ships_the_nsis_installer_alone(self) -> None:
        self.assertEqual(conf("tauri.windows.conf.json")["bundle"]["targets"], ["nsis"])

    def test_per_user_with_the_stock_template_and_one_hook_file(self) -> None:
        nsis = conf("tauri.conf.json")["bundle"]["windows"]["nsis"]
        self.assertEqual(nsis["installMode"], "currentUser")
        self.assertEqual(nsis["installerHooks"], HOOKS)
        for name in sorted(TAURI.glob("tauri*.conf.json")):
            with self.subTest(name.name):
                windows = conf(name.name).get("bundle", {}).get("windows", {})
                self.assertNotIn("template", windows.get("nsis", {}))
                if name.name != "tauri.conf.json":
                    self.assertNotIn("installerHooks", windows.get("nsis", {}))
                self.assertNotIn(
                    "wix", windows, "an MSI's uninstaller is not the audited one"
                )

    def test_the_hook_removes_the_runtime_and_nothing_of_the_owners(self) -> None:
        lines = hook_lines()
        runtime = "$LOCALAPPDATA\\${BUNDLEID}\\runtime"
        link = runtime + "\\app\\demo\\castle-radio\\.radio-data"
        self.assertEqual(
            lines,
            [
                "!macro NSIS_HOOK_POSTUNINSTALL",
                "${If} $UpdateMode <> 1",
                "${AndIf} $EXEDIR != $INSTDIR",
                "SetShellVarContext current",
                f'RMDir "{link}"',
                f'${{IfNot}} ${{FileExists}} "{link}\\*.*"',
                f'RMDir /r "{runtime}"',
                "${EndIf}",
                "${EndIf}",
                "!macroend",
            ],
            "one hook: after the uninstall, not on an update or an upgrade's "
            "in-place uninstall — the link to the songs alone, then the "
            "runtime folder only once nothing answers through that link",
        )
        code = "\n".join(lines)
        for owners in ("$APPDATA", "settings", "logs", "Delete "):
            self.assertNotIn(owners, code)
        self.assertEqual(len(re.findall(r"\bRMDir /r\b", code, re.IGNORECASE)), 1)

    def test_the_link_it_unlinks_first_is_the_one_the_installer_makes(self) -> None:
        """RMDir /r follows a junction into its target: the first Windows
        release smoke of this hook emptied the owner's data through
        Castle Radio's. The link is desktop_env's RADIO_DATA under the
        runtime's app/, and link_radio_data is the only directory link any
        shipped tool makes — a second one needs a line in the hook first."""
        app = de.Dirs(Path("R"), Path("D"), "Windows").app
        self.assertEqual(
            app / de.RADIO_DATA, Path("R", "app", "demo", "castle-radio", ".radio-data")
        )
        makers = []
        for tree in ("tools", "installer", "demo/castle-radio"):
            for path in sorted((ROOT / tree).rglob("*.py")):
                if path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8")
                if "mklink" in text or "target_is_directory=True" in text:
                    makers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(makers, ["tools/desktop_env.py"])

    def test_the_runtime_it_names_is_the_one_the_app_sets_up(self) -> None:
        """${BUNDLEID} is the identifier, and src/lib.rs puts the runtime in
        Tauri's app_local_data_dir — %LOCALAPPDATA%\\<identifier> on Windows."""
        lib = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
        self.assertIn('runtime_dir: paths.app_local_data_dir()?.join("runtime"),', lib)
        self.assertEqual(conf("tauri.conf.json")["identifier"], IDENTIFIER)

    def test_the_data_directory_is_named_after_this_identifier(self) -> None:
        readme = (ROOT / "desktop" / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "OWNER-GUIDE.md").read_text(encoding="utf-8")
        for path in (
            f"%APPDATA%\\{IDENTIFIER}",
            f"%LOCALAPPDATA%\\{IDENTIFIER}\\runtime",
            f"~/Library/Application Support/{IDENTIFIER}",
        ):
            self.assertIn(path, readme)
        # The owner is told the one folder a Mac keeps after the app goes.
        self.assertIn(f"~/Library/Application Support/{IDENTIFIER}/runtime", guide)

    def test_the_cli_is_the_one_whose_template_was_read(self) -> None:
        pin = json.loads(CLI.read_text(encoding="utf-8"))["devDependencies"]
        self.assertEqual(
            pin["@tauri-apps/cli"],
            AUDITED_CLI,
            "a new Tauri CLI brings a new NSIS template: re-read its uninstall "
            "section (the delete-app-data box, its RmDir, where the hooks run "
            "and what `_?=` means) before moving AUDITED_CLI",
        )


if __name__ == "__main__":
    unittest.main()
