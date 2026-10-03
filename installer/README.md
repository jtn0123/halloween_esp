# Castle Tools installer (Windows and macOS)

Castle Tools is the computer half of the castle: import songs (from a file
or a link), split voice from music, preview the lights, and send songs to
the castle's card. The castle itself plays the show without any of this —
you only need it to add or change songs.

This folder installs it for one user, with no administrator rights. It
downloads about 1 GB (Python, PyTorch for the voice splitter, its model,
ffmpeg, yt-dlp) and needs an internet connection the first time.

## What you need

- **Windows 10 or 11** (64-bit Intel/AMD), or **a Mac with Apple Silicon**
  (M1 or newer). Intel Macs are not supported: the voice splitter's
  libraries no longer ship for them.
- The Castle Tools zip from the
  [Releases page](https://github.com/jtn0123/halloween_esp/releases)
  (`Source code (zip)` of the newest release), unpacked anywhere — your
  Downloads folder is fine. The installer copies what it needs; you can
  delete the unpacked folder afterwards.

## Windows

1. Right-click the downloaded zip → **Extract All…** → **Extract**.
2. Open the extracted folder, then the `installer` folder, and double-click
   **install.cmd**.
3. Windows shows **"Windows protected your PC"** (SmartScreen) because the
   installer is not code-signed. Click **More info**, then **Run anyway**.
   You only do this once.
4. A black window shows the progress. When it says
   **Castle Tools are installed.**, press a key to close it.
5. Start it any time from the **Start menu → Castle Tools**. Your browser
   opens at `http://127.0.0.1:8871/`. Keep the small black window open while
   you work; closing it stops Castle Tools.

Windows Firewall does not ask anything: Castle Tools only listens on this
computer (127.0.0.1), never on the network.

## macOS

1. Double-click the downloaded zip to unpack it.
2. Open **Terminal** (Applications → Utilities), type `sh ` (with a space),
   drag **install.sh** from the unpacked `installer` folder into the
   Terminal window, and press Return. Running it this way means macOS does
   not need to approve the script.
3. When it says **Castle Tools are installed.**, close Terminal.
4. Start it from Finder: **Go → Home → Applications → CastleTools →
   Castle Tools.command** (drag it to the Dock to keep it handy). Your
   browser opens at `http://127.0.0.1:8871/`. Keep the Terminal window it
   opens; closing it stops Castle Tools.

If you double-click a downloaded script or app instead and macOS says it
**"cannot be opened because Apple cannot check it for malicious software"**
(Gatekeeper — the files are not signed by an Apple developer account):
click **Done**, open **System Settings → Privacy & Security**, scroll down
to the message about it, click **Open Anyway**, and confirm with your
password. (On macOS 15 and later, right-click → Open no longer skips this.)
The installed `Castle Tools.command` does not need this step: the installer
places it itself.

## Everyday use

- **Starting twice is fine.** A second start sees Castle Tools already
  running and only opens the browser.
- **Your castle's address**: give it once with
  `--castle-host <address>` (for example `castle-feather-s3.local` or
  `192.168.1.50`) when you run the installer. It is saved in your settings.
- **Your castle's key** (if you locked it): enter it once under *Settings* in
  Castle Radio or the light desk's 🏰 panel. It is remembered in
  `devices.toml` beside your settings, for that castle only.
- **Updates**: Castle Tools checks GitHub for a new release at most once a
  day and says so in its window. To update, run the installer again with
  `--update` (Windows: open a Command Prompt in the `installer` folder and
  run `install.cmd --update`; macOS: `sh install.sh --update`).

## Where things are

| | Windows | macOS |
|---|---|---|
| The app (replaced by updates) | `%LOCALAPPDATA%\Programs\CastleTools` | `~/Applications/CastleTools` |
| Your songs, show and settings | `%LOCALAPPDATA%\CastleTools` | `~/Library/Application Support/CastleTools` |

Your songs and your show (`scenes.yaml`) are never inside the app folder,
so updating, repairing or uninstalling the app does not touch them. The
first install copies the castle's shipped show into your folder; it never
overwrites a show you already have.

## Installer options

Add these after `install.cmd` (Windows) or `sh install.sh` (macOS):

| Option | What it does |
|---|---|
| *(none)* | Install, or finish an install that was interrupted. Safe to re-run. |
| `--repair` | Reinstall the Python environment and re-download the castle-core programs, yt-dlp, and ffmpeg if the installer downloaded it. |
| `--update` | Install the newest release from GitHub, if it is newer than yours. |
| `--uninstall` | Remove the app. **Keeps** your songs, show and settings. |
| `--uninstall --purge` | Remove the app **and** your songs, show and settings. |
| `--castle-host <address>` | Remember your castle's address. |
| `--dry-run` | Print what would happen and change nothing. |
| `--no-shortcut` | Windows only: do not add the Start-menu entry. |
| `--from-source` | Build the castle-core programs with Rust (`cargo`) instead of downloading them. For developers. |
| `--help` | Every option. |

uv (the Python installer this uses) and its Python stay installed after an
uninstall; other programs may share them. Remove them with
`uv python uninstall 3.13` and by deleting `uv` from `~/.local/bin`
(Windows: `%USERPROFILE%\.local\bin`) if nothing else needs them.

## For developers

- `installer/install.sh` and `install.ps1` only install uv and Python 3.13,
  then run `tools/desktop_install.py`, which does everything else on both
  platforms. The launchers run `tools/desktop_launch.py`; both share
  `tools/desktop_env.py` for paths and environment. Tests:
  `tests/test_desktop_*.py`, `tests/test_lock_desktop.py`.
- Python packages come from `requirements-desktop.lock` (hash-pinned,
  macOS arm64 + Windows x64), regenerated with `make lock-desktop`.
- castle-core binaries come from the release asset
  `castle-core-<rust-target>-<tag>.zip`, checked against the release's
  `SHA256SUMS`. A tree whose tag is unknown takes the latest release's.
- `CASTLE_TOOLS_HOME` / `CASTLE_TOOLS_DATA` (or `--prefix` / `--data-dir`)
  move the two folders, e.g. into a throwaway directory for a trial run.
