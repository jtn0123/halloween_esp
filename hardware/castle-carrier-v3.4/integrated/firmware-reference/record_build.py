"""Bind compilation and stereo settings to exact inputs; never flash hardware."""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def match(pattern: str, text: str) -> str:
    result = re.search(pattern, text)
    if result is None:
        raise ValueError("Missing build metadata: " + pattern)
    return result[1]


def verify_settings(
    cpp: str, definitions: str, config: str, sdk: str, variant: str
) -> None:
    require(
        bool(re.search(r"set_slot_mode\((?:::)?I2S_SLOT_MODE_STEREO\)", cpp)),
        "Stereo slot mode missing",
    )
    require(
        bool(re.search(r"set_std_slot_mask\((?:::)?I2S_STD_SLOT_BOTH\)", cpp)),
        "Both stereo slots missing",
    )
    require("set_announcement_format(" in cpp, "Announcement format missing")
    fmt = cpp.split("set_announcement_format(", 1)[1].split("});", 1)[0]
    require(
        bool(re.search(r"\.num_channels\s*=\s*2", fmt)), "Announcement is not stereo"
    )
    for item, pin in [
        ("castle_speaker->set_dout_pin", 15),
        ("i2s_bus->set_bclk_pin", 11),
        ("i2s_bus->set_lrclk_pin", 12),
    ]:
        require(f"{item}({pin})" in cpp, "Wrong audio pin: " + item)
    for codec in ["MP3", "WAV", "OPUS"]:
        require(
            "#define USE_AUDIO_" + codec + "_SUPPORT" in definitions,
            "Missing codec: " + codec,
        )
    for setting in [
        "castle_rate_safe_speaker->set_target_sample_rate(44100)",
        "castle_rate_safe_speaker->set_output_speaker(castle_speaker)",
        "castle_media->set_announcement_speaker(castle_rate_safe_speaker)",
        'App.pre_setup("castle-v34-' + variant + '"',
    ]:
        require(setting in cpp, "Missing audio/device setting: " + setting)
    require(
        "channel: stereo" in config and "num_channels: 2" in config,
        "Overlay is not stereo",
    )
    for setting in ["CONFIG_SPIRAM_MODE_OCT=y", "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y"]:
        require(setting in sdk, "Wrong flash/PSRAM setting: " + setting)


def build_receipt(board: Path, workspace: Path, source_commit: str) -> dict:
    log = (workspace / "build.log").read_text(encoding="utf-8")
    require(
        "Successfully compiled program." in log or "[SUCCESS]" in log,
        "Compilation did not succeed",
    )
    build = workspace / "build"
    cpp = (build / "src/main.cpp").read_text(encoding="utf-8")
    definitions = (build / "src/esphome/core/defines.h").read_text(encoding="utf-8")
    cfg = board / "firmware-reference/castle_v34.yaml"
    config = cfg.read_text(encoding="utf-8")
    sdk = (build / "sdkconfig.castle-v34-integrated").read_text(encoding="utf-8")
    verify_settings(cpp, definitions, config, sdk, board.name)
    artifacts = {}
    for name in ["firmware.elf", "firmware.ota.bin", "firmware.factory.bin"]:
        files = list(build.rglob(name))
        require(len(files) == 1, "Expected exactly one artifact: " + name)
        artifacts[name] = digest(files[0])
    src = workspace / "firmware"
    sources = {
        q.relative_to(src).as_posix(): digest(q)
        for q in sorted(src.rglob("*"))
        if q.is_file()
        and q.suffix in [".yaml", ".h", ".cpp"]
        and q.name != "secrets.yaml"
        and ".esphome" not in q.parts
    }
    require(bool(sources), "Firmware source snapshot is empty")
    return {
        "status": "PASS",
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "scope": "Compilation and generated-code configuration only; dummy credentials; no flashing or physical stereo test",
        "variant": board.name,
        "esphome_version": match(r"INFO ESPHome ([^\s]+)", log),
        "idf_version": match(r"Checking ESP-IDF ([^\s]+) framework", log),
        "ram_summary": [
            line.strip() for line in log.splitlines() if line.strip().startswith("RAM:")
        ],
        "flash_summary": [
            line.strip()
            for line in log.splitlines()
            if line.strip().startswith("Flash:")
        ],
        "config_sha256": digest(cfg),
        "artifacts": artifacts,
        "stereo": {
            "slot_mode": "I2S_SLOT_MODE_STEREO",
            "slot_mask": "I2S_STD_SLOT_BOTH",
            "announcement_pipeline_channels": 2,
            "shared_data_pin": "GPIO15",
        },
        "audio_compatibility": {
            "codecs": ["MP3", "WAV", "OPUS"],
            "pipeline_speaker": "castle_rate_safe_speaker",
            "output_sample_rate_hz": 44100,
            "bits_per_sample": 16,
            "buffer_duration_ms": 100,
            "scope": "Compiled configuration, not runtime playback or CPU/heap qualification",
        },
        "device_name": "castle-v34-" + board.name,
        "firmware_version": match(r"(?m)^ {2}version: [\"\']([^\"\']+)", config),
        "firmware_source_files_sha256": sources,
        "source_commit": source_commit,
        "main_cpp_sha256": digest(build / "src/main.cpp"),
    }


def main() -> None:
    require(len(sys.argv) <= 2, "Expected at most one source repository path")
    board = Path(__file__).resolve().parent.parent
    workspace = board / "local-checks/firmware"
    source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else board.parents[2]
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    report = build_receipt(board, workspace, commit)
    # The output location is fixed beside this handoff, never a CLI-supplied path.
    (workspace / "firmware-build-check.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print("Recorded fresh stereo compile", board.name, report["flash_summary"])


if __name__ == "__main__":
    main()
