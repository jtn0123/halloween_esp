"""Register a tiny macOS URL launcher; does not install audio dependencies."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCH_SERVICES = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


def launcher_info(root: Path) -> dict:
    """Persist the trusted local checkout, never a path supplied by a URL."""
    return {
        "CFBundleIdentifier": "local.castle.tools.launcher",
        "CFBundleName": "Castle Tools",
        "CFBundleExecutable": "CastleTools",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "LSUIElement": True,
        "CastleProjectPath": str(root.resolve()),
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "Castle Tools startup",
                "CFBundleURLSchemes": ["castle-tools"],
            }
        ],
    }


def register(root: Path = ROOT, applications: Path | None = None) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("Website startup registration requires macOS.")
    if not (root / "Open Castle Studio.command").is_file():
        raise RuntimeError("The Castle Studio launcher is missing from this checkout.")
    destination = (applications or Path.home() / "Applications") / "Castle Tools.app"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Compile before replacing a working launcher. Re-registration follows a
    # moved checkout without changing packages, models, or login items.
    with tempfile.TemporaryDirectory(prefix="castle-launcher-") as temporary:
        bundle = Path(temporary) / destination.name
        contents = bundle / "Contents"
        binary = contents / "MacOS" / "CastleTools"
        binary.parent.mkdir(parents=True)
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                "-parse-as-library",
                str(root / "tools" / "castle_launcher.swift"),
                "-o",
                str(binary),
                "-framework",
                "AppKit",
            ],
            check=True,
        )
        (contents / "Info.plist").write_bytes(plistlib.dumps(launcher_info(root)))
        subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True)
        shutil.copytree(bundle, destination, dirs_exist_ok=True)
    subprocess.run([str(LAUNCH_SERVICES), "-f", str(destination)], check=True)
    return destination


if __name__ == "__main__":
    try:
        print(f"Website startup is ready: {register()}")
        print("On the castle website, click Start Mac tools, then Connect Mac tools.")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Could not register Castle Tools: {error}", file=sys.stderr)
        print(
            "Install Apple's Command Line Tools if xcrun/swiftc is missing.",
            file=sys.stderr,
        )
        sys.exit(1)
