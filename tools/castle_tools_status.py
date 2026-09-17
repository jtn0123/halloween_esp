"""Cheap readiness probe shared by Castle Radio and its desktop launcher."""

from __future__ import annotations

import argparse
import functools
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = "htdemucs model"
INSTALL_COMMAND = "./tools/install_castle_tools.sh"


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


def _command(name: str, required: bool = True) -> Check:
    path = shutil.which(name)
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
    binary = ROOT / "core" / "target" / "release" / "analyze_track"
    ok = binary.is_file() and os.access(binary, os.X_OK)
    return {
        "name": "analyze_track",
        "ok": ok,
        "detail": str(binary) if ok else "not built",
        "required": True,
    }


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
            bag = yaml.safe_load(bag_file.read_text())
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
    checks = [
        _python(),
        _package("numpy", "numpy"),
        _package("scipy", "scipy"),
        _package("PyYAML", "yaml"),
        _command("ffmpeg"),
        _command("yt-dlp", False),
        _command("cargo", False),
        _analyzer(),
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
        "install_command": INSTALL_COMMAND,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-core", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--human", action="store_true")
    args = parser.parse_args()
    result = status()
    if args.human:
        if result["ready"]:
            print("Castle Tools are ready.")
        else:
            print("Castle Tools need attention:")
            checks = result["checks"]
            assert isinstance(checks, list)
            for check in checks:
                if not check["ok"]:
                    print(f"  - {check['name']}: {check['detail']}")
            print(f"Run {result['install_command']} to install or repair them.")
    else:
        print(json.dumps(result, indent=2))
    failed = (args.require_core and not result["core_ready"]) or (
        args.require_ready and not result["ready"]
    )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
