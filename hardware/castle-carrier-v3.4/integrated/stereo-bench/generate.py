"""Generate and decode-check a stereo fixture; never access a device."""

from __future__ import annotations

import array
import hashlib
import json
import math
import subprocess
import sys
import wave
from pathlib import Path

RATE = 44100
MP3_NAME = "channel-identification.mp3"
WAV_NAME = "channel-identification.wav"


def samples() -> array.array:
    data = array.array("h")
    for i in range(RATE * 9):
        t = i / RATE
        local = t % 3
        env = min(1, local / 0.03, max(0, (2 - local) / 0.03)) if local < 2 else 0
        val = int(
            6500 * env * math.sin(2 * math.pi * (440 if t < 3 or t >= 6 else 660) * t)
        )
        data.extend((val if t < 3 or t >= 6 else 0, val if t >= 3 else 0))
    return data


def check_channels(data: array.array) -> tuple[dict, dict]:
    if len(data) < RATE * 8 * 2:
        raise ValueError("Decoded stereo fixture is too short")
    checks, energies = {}, {}
    for name, t0, t1, expected in [
        ("left_only", 0.25, 1.75, 0),
        ("right_only", 3.25, 4.75, 1),
        ("same_both", 6.25, 7.75, None),
    ]:
        energy = [
            sum(
                float(data[i * 2 + channel]) ** 2
                for i in range(int(t0 * RATE), int(t1 * RATE))
            )
            / int((t1 - t0) * RATE)
            for channel in [0, 1]
        ]
        energies[name] = energy
        if max(energy) == 0:
            checks[name] = False
        elif expected is None:
            checks[name] = abs(energy[0] - energy[1]) / max(energy) < 0.01
        else:
            checks[name] = (
                min(energy) / max(energy) < 0.001 and energy[expected] > 10000
            )
    return checks, energies


def generate(directory: Path) -> dict:
    directory.mkdir(exist_ok=True)
    data = samples()
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(directory / WAV_NAME), "wb") as file:
        file.setnchannels(2)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes(data.tobytes())
    mp3 = directory / MP3_NAME
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(directory / WAV_NAME),
            "-ar",
            str(RATE),
            "-ac",
            "2",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(mp3),
        ],
        check=True,
    )
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(mp3),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    pcm = array.array("h")
    pcm.frombytes(decoded.stdout)
    if sys.byteorder != "little":
        pcm.byteswap()
    checks, energies = check_channels(pcm)
    report = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "decoded_channel_energy": energies,
        "audio": {
            "channels": 2,
            "sample_rate": RATE,
            "sequence_seconds": {
                "0-2": "LEFT only, 440 Hz",
                "2-3": "silence",
                "3-5": "RIGHT only, 660 Hz",
                "5-6": "silence",
                "6-8": "same 440 Hz on both",
                "8-9": "silence",
            },
        },
        "physical_playback": "NOT_PERFORMED",
        "mp3_sha256": hashlib.sha256(mp3.read_bytes()).hexdigest(),
    }
    (directory / "fixture-check.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    if report["status"] != "PASS":
        raise ValueError("Decoded fixture does not preserve stereo channels")
    return report


def main() -> None:
    generate(Path(__file__).resolve().parent)
    print("Verified stereo bench fixture: left only, right only, then identical both.")


if __name__ == "__main__":
    main()
