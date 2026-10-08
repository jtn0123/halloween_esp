"""Bind successful compilation and resolved stereo settings to exact local inputs."""

from pathlib import Path
import json, hashlib, re, sys, datetime

p = Path.cwd()
root = Path(sys.argv[1])
log = (root / "build.log").read_text()
build = root / "build"
cpp = (build / "src/main.cpp").read_text()
assert "Successfully compiled program." in log or "[SUCCESS]" in log
assert re.search(r"set_slot_mode\((?:::)?I2S_SLOT_MODE_STEREO\)", cpp)
assert re.search(r"set_std_slot_mask\((?:::)?I2S_STD_SLOT_BOTH\)", cpp)
fmt = cpp.split("set_announcement_format(", 1)[1].split("});", 1)[0]
assert re.search(r"\.num_channels\s*=\s*2", fmt), fmt
for item, pin in [
    ("castle_speaker->set_dout_pin", 15),
    ("i2s_bus->set_bclk_pin", 11),
    ("i2s_bus->set_lrclk_pin", 12),
]:
    assert f"{item}({pin})" in cpp, (item, pin)
defs = (build / "src/esphome/core/defines.h").read_text()
assert all(
    "#define USE_AUDIO_" + codec + "_SUPPORT" in defs
    for codec in ["MP3", "WAV", "OPUS"]
)
assert "castle_rate_safe_speaker->set_target_sample_rate(44100)" in cpp
assert "castle_rate_safe_speaker->set_output_speaker(castle_speaker)" in cpp
assert "castle_media->set_announcement_speaker(castle_rate_safe_speaker)" in cpp
assert 'App.pre_setup("castle-v34-' + p.name + '"' in cpp
cfg = p / "firmware-reference/castle_v34.yaml"
data = cfg.read_text()
assert "channel: stereo" in data and "num_channels: 2" in data
sdk = (build / "sdkconfig.castle-v34-integrated").read_text()
assert "CONFIG_SPIRAM_MODE_OCT=y" in sdk
assert "CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y" in sdk
hashfile = lambda q: hashlib.sha256(q.read_bytes()).hexdigest()
artifacts = {}
for name in ["firmware.elf", "firmware.ota.bin", "firmware.factory.bin"]:
    files = list(build.rglob(name))
    assert len(files) == 1, (name, files)
    artifacts[name] = hashfile(files[0])
src = root / "firmware"
source_hashes = {
    str(q.relative_to(src)): hashfile(q)
    for q in sorted(src.rglob("*"))
    if q.is_file()
    and q.suffix in [".yaml", ".h", ".cpp"]
    and q.name != "secrets.yaml"
    and ".esphome" not in q.parts
}
report = {
    "status": "PASS",
    "generated_at": datetime.datetime.now().astimezone().isoformat(),
    "scope": "Compilation and generated-code configuration only; dummy credentials; no flashing or physical stereo test",
    "variant": p.name,
    "esphome_version": re.search(r"INFO ESPHome ([^\s]+)", log)[1],
    "idf_version": re.search(r"Checking ESP-IDF ([^\s]+) framework", log)[1],
    "ram_summary": [
        l.strip() for l in log.splitlines() if l.strip().startswith("RAM:")
    ],
    "flash_summary": [
        l.strip() for l in log.splitlines() if l.strip().startswith("Flash:")
    ],
    "config_sha256": hashfile(cfg),
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
    "device_name": "castle-v34-" + p.name,
    "firmware_version": re.search(r"(?m)^  version: [\"\']([^\"\']+)", data)[1],
    "firmware_source_files_sha256": source_hashes,
    "source_commit": __import__("subprocess")
    .check_output(["git", "-C", sys.argv[2], "rev-parse", "HEAD"], text=True)
    .strip(),
    "main_cpp_sha256": hashfile(build / "src/main.cpp"),
}
(root / "firmware-build-check.json").write_text(json.dumps(report, indent=2) + "\n")
print("Recorded fresh stereo compile", p.name, report["flash_summary"])
