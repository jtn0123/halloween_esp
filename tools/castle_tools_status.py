"""Cheap readiness probe shared by Castle Radio and its desktop launcher."""

from __future__ import annotations

import argparse
import functools
import importlib.metadata
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import TypedDict

import exe_paths
from exe_paths import exe

ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = "htdemucs model"


def install_command(platform: str | None = None) -> str:
    """How this platform installs or repairs the tools: the installer/ folder,
    which every install carries a copy of (desktop_install.py places its
    launcher from it). Re-running it resumes; it never starts over."""
    platform = platform or sys.platform
    if platform == "win32":
        return r"installer\install.cmd"
    if platform == "darwin":
        return "sh installer/install.sh"
    # No release ships a prebuilt castle-core for Linux (installer/install.sh).
    return "sh installer/install.sh --from-source"


def website_startup(platform: str | None = None) -> str | None:
    """The double-click that registers the castle-tools:// helper, or None
    where there is none: it compiles a macOS URL handler with Apple's tools
    (register_castle_launcher.py), so elsewhere the page must not offer it."""
    return (
        "Enable Website Startup.command"
        if (platform or sys.platform) == "darwin"
        else None
    )


def in_app() -> bool:
    """Whether this Castle Radio is the desktop app's: its supervisor sets
    CASTLE_APP_VERSION (src-tauri/src/supervisor.rs). The app carries no
    installer/ folder and no website-startup double-click — it registers
    castle-tools:// itself — so neither is offered there."""
    return bool(os.environ.get("CASTLE_APP_VERSION", "").strip())


#: Where each system shows the app's tray menu (src-tauri/src/tray.rs).
TRAY = {
    "darwin": "choose Repair Castle Tools… from the ♜ in the menu bar",
    "win32": (
        "right-click the Castle Tools icon in the notification area and "
        "choose Repair Castle Tools…"
    ),
}


def repair_words(platform: str | None = None) -> str | None:
    """How the desktop app's owner repairs it — the tray's Repair, which runs
    the app's own setup again — or None outside the app, where the
    installer's command (install_command) is the repair."""
    if not in_app():
        return None
    where = TRAY.get(platform or sys.platform, TRAY["win32"])
    return (
        f"To repair Castle Tools, {where}. It sets the tools up again from "
        "the internet, takes a few minutes, and keeps your songs."
    )


class Check(TypedDict):
    name: str
    ok: bool
    detail: str
    required: bool


def _package(name: str, module: str, required: bool = True) -> Check:
    found = importlib.util.find_spec(module) is not None
    try:
        version = importlib.metadata.version(name) if found else ""
    except importlib.metadata.PackageNotFoundError:
        version = "installed" if found else ""
    return {
        "name": name,
        "ok": found,
        "detail": version or "missing",
        "required": required,
    }


def _command(name: str, required: bool = True, command: str | None = None) -> Check:
    """`name` as the user knows it; `command` what is actually run when a
    bundled copy (CASTLE_FFMPEG, CASTLE_YTDLP) stands in for PATH's."""
    path = exe_paths.which(command or name)
    return {
        "name": name,
        "ok": path is not None,
        "detail": path or "missing",
        "required": required,
    }


def _python() -> Check:
    ok = sys.version_info >= (3, 13)
    version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    return {"name": "Python 3.13+", "ok": ok, "detail": version, "required": True}


def _analyzer() -> Check:
    binary = ROOT / "core" / "target" / "release" / exe("analyze_track")
    ok = binary.is_file() and os.access(binary, os.X_OK)
    return {
        "name": "analyze_track",
        "ok": ok,
        "detail": str(binary) if ok else "not built",
        "required": True,
    }


def _cargo(analyzer: Check) -> Check:
    """cargo only ever BUILDS castle-core (tools/core_bins.py). Where
    analyze_track is already in place — every release install, whose
    binaries came prebuilt — a missing cargo is nothing to fix, and saying
    "needs attention" sent every buyer's readiness card amber over a tool
    they will never need (tests/install_smoke.py found it)."""
    check = _command("cargo", False)
    if not check["ok"] and analyzer["ok"]:
        return {**check, "ok": True, "detail": "not needed: castle-core is built"}
    return check


def _hf_cache() -> Path:
    if explicit := os.environ.get("HF_HUB_CACHE"):
        return Path(explicit).expanduser()
    if hf_home := os.environ.get("HF_HOME"):
        return Path(hf_home).expanduser() / "hub"
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()
    return cache / "huggingface" / "hub"


def _model() -> Check:
    """Verify the complete Demucs 4.1 HF bag without loading torch or networking."""
    try:
        import yaml
    except ImportError:
        return {
            "name": MODEL_NAME,
            "ok": False,
            "detail": "PyYAML is missing; model cache cannot be checked",
            "required": False,
        }
    snapshots = _hf_cache() / "models--adefossez--HTDemucs" / "snapshots"
    try:
        candidates = list(snapshots.glob("*/htdemucs.yaml"))
    except OSError:
        candidates = []
    for bag_file in candidates:
        try:
            bag = yaml.safe_load(bag_file.read_text(encoding="utf-8"))
            signatures = bag.get("models", []) if isinstance(bag, dict) else []
            weights = [bag_file.parent / f"{sig}.safetensors" for sig in signatures]
            if signatures and all(
                path.is_file() and path.stat().st_size for path in weights
            ):
                return {
                    "name": MODEL_NAME,
                    "ok": True,
                    "detail": f"cached ({len(weights)} model file(s))",
                    "required": False,
                }
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            continue
    return {
        "name": MODEL_NAME,
        "ok": False,
        "detail": "not fully cached; run the installer to download it",
        "required": False,
    }


@functools.lru_cache(maxsize=1)
def status() -> dict[str, object]:
    """Return JSON-safe readiness and feature capability details."""
    analyzer = _analyzer()
    checks = [
        _python(),
        _package("numpy", "numpy"),
        _package("scipy", "scipy"),
        _package("PyYAML", "yaml"),
        _command("ffmpeg", command=exe_paths.ffmpeg()),
        _command("yt-dlp", False, exe_paths.ytdlp()),
        _cargo(analyzer),
        analyzer,
        _package("demucs", "demucs", False),
        _package("torch", "torch", False),
        _model(),
    ]
    by_name = {item["name"]: item["ok"] for item in checks}
    importing = all(
        by_name[name]
        for name in (
            "Python 3.13+",
            "numpy",
            "scipy",
            "PyYAML",
            "ffmpeg",
            "analyze_track",
        )
    )
    separating = importing and all(
        by_name[name] for name in ("demucs", "torch", MODEL_NAME)
    )
    return {
        "service": "castle-radio",
        "protocol": 1,
        "ready": all(item["ok"] for item in checks),
        "core_ready": all(item["ok"] for item in checks if item["required"]),
        "capabilities": {
            "importing": importing,
            "url_importing": importing and by_name["yt-dlp"],
            "separation": separating,
        },
        "checks": checks,
        "install_command": None if in_app() else install_command(),
        "website_startup": None if in_app() else website_startup(),
        "repair": repair_words(),
    }


def human(result: dict[str, object]) -> None:
    """--human: ready, or what needs attention and how to fix it."""
    if result["ready"]:
        print("Castle Tools are ready.")
        return
    print("Castle Tools need attention:")
    checks = result["checks"]
    assert isinstance(checks, list)
    for check in checks:
        if not check["ok"]:
            print(f"  - {check['name']}: {check['detail']}")
    if result.get("repair"):
        print(result["repair"])
    else:
        print(
            f"Run {result['install_command']} in the Castle Tools folder"
            " to install or repair them."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-core", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--human", action="store_true")
    args = parser.parse_args()
    result = status()
    if args.human:
        human(result)
    else:
        print(json.dumps(result, indent=2))
    failed = (args.require_core and not result["core_ready"]) or (
        args.require_ready and not result["ready"]
    )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
