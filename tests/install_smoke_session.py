"""The `session` phase of tests/install_smoke.py: a buyer's first evening.

The installed launcher starts Castle Radio and the installed tree's
tools/castle_emu.py stands in for the castle. Then, through the page's own
routes (install_smoke_radio.Radio): the readiness card, Find my castle
(a browse, then the typed address), three songs made by the installed ffmpeg
and dropped on the import panel, one of them split by Demucs and timed, each
sent to the castle and checked on its card, and one played with its show.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import install_smoke_env as se  # tools/ on the path, then:

# isort: split
import install_smoke_radio as radio
from install_smoke_buyer import Buyer
from install_smoke_env import check


def cpu_name() -> str:
    """The runner's processor as the OS names it, for the Demucs line."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, check=True,
            )  # fmt: skip
            return out.stdout.strip()
        if sys.platform == "win32":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return platform.processor() or platform.machine()


def demucs_report(song: radio.Song, trail: list[tuple[float, str]]) -> dict[str, Any]:
    """The split's timings from the job's phase trail, as a row."""
    spans = radio.phase_spans(trail)
    separating = spans.get(radio.SEPARATING, 0.0)
    step = sum(spans.get(p, 0.0) for p in radio.SPLIT_PHASES)
    return {
        "runner": os.environ.get("RUNNER_OS", platform.system()),
        "cpu": cpu_name(),
        "cores": os.cpu_count() or 0,
        "audio_s": song.seconds,
        "separate_s": round(separating, 1),
        "split_step_s": round(step, 1),
        "whole_import_s": round(trail[-1][0] - trail[0][0], 1),
    }


def say_demucs(row: dict[str, Any], work: Path) -> None:
    line = (
        f"DEMUCS {row['runner']} ({row['cpu']}, {row['cores']} cores): "
        f"{row['audio_s']} s of audio separated in {row['separate_s']} s; "
        f"the whole split step (separate + encode + analyse) {row['split_step_s']} s; "
        f"the import end to end {row['whole_import_s']} s"
    )
    print(line, flush=True)
    (work / "logs" / "demucs.json").write_text(json.dumps(row) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(
                "| runner | CPU | cores | audio | Demucs | split step | import |\n"
                "|---|---|---|---|---|---|---|\n"
                f"| {row['runner']} | {row['cpu']} | {row['cores']} | "
                f"{row['audio_s']} s | {row['separate_s']} s | "
                f"{row['split_step_s']} s | {row['whole_import_s']} s |\n"
            )


def find_castle(r: radio.Radio, port: int) -> None:
    """Find my castle: the LAN browse answers (whatever it finds on a
    runner), then the typed address is adopted because it IS a castle."""
    status, found = r.call(
        "/radio/device/find", b"{}", {"Content-Type": "application/json"}, 30
    )
    print(f"Find my castle browse: {status} {found}")
    check(status in (200, 502) and isinstance(found, dict), "the LAN browse answers")
    host = f"127.0.0.1:{port}"
    adopted = r.post("/radio/device/address", {"host": host})
    check(adopted.get("host") == host, f"Find my castle adopts the typed {host}")
    device = r.get("/radio/device")
    check(
        device.get("connected") is not False,
        f"Castle Radio reaches the castle {device}",
    )


def import_songs(b: Buyer, r: radio.Radio) -> dict[str, dict[str, Any]]:
    """Make the three songs with the installed ffmpeg, drop them on the
    import panel, wait as the queue does. Returns the finished jobs by name."""
    ffmpeg = str(b.record()["ffmpeg"])
    folder = b.work / "My Music"
    folder.mkdir(parents=True, exist_ok=True)
    ids: dict[str, str] = {}
    for song in radio.SONGS:
        path = folder / song.name
        subprocess.run(radio.tone_command(ffmpeg, path, song), check=True, env=b.env)
        check(path.stat().st_size > 1000, f"ffmpeg made {song.name!r}")
        ids[song.name] = str(r.upload(path, song)["id"])
    final, trails = r.wait_jobs(list(ids.values()))
    jobs = {name: final[i] for name, i in ids.items()}
    for song in radio.SONGS:
        job = jobs[song.name]
        said = (job["phase"], job.get("error"), job.get("error_detail"))
        detail = ": ".join(str(part) for part in said if part)
        check(job["phase"] == "Ready on this computer" and not job.get("error"),
              f"{song.name!r} imports ({detail})")  # fmt: skip
        result = job["result"]
        check(result["title"] == radio.page_title(song.name),
              f"{song.name!r} keeps its title as {result['title']!r}")  # fmt: skip
        check(result["split"] is song.split, f"{song.name!r} split={song.split}")
        if song.split:
            say_demucs(demucs_report(song, trails[ids[song.name]]), b.work)
    return jobs


def send_and_play(
    b: Buyer, r: radio.Radio, jobs: dict[str, dict[str, Any]], port: int, card: Path
) -> None:
    for name, job in jobs.items():
        key, filename = job["result"]["key"], job["result"]["playback_file"]
        done = r.sync(key)
        check(done.get("error") is None and done["phase"] == "Audio and show verified on castle",
              f"{name!r} sends to the castle ({done['phase']}{': ' + str(done['error']) if done.get('error') else ''})")  # fmt: skip
        problems = radio.card_problems(card, b.dirs.tracks, filename)
        check(not problems, f"{name!r} is whole on the card {problems}")
    inventory = r.get("/radio/device/library")["tracks"]
    for name, job in jobs.items():
        state = inventory[job["result"]["key"]]["status"]
        check(state == "ready", f"the castle page lists {name!r} as {state}")
    name, job = next((n, j) for n, j in jobs.items() if j["result"]["split"])
    key, filename = job["result"]["key"], job["result"]["playback_file"]
    r.post("/radio/device/command", {"action": "file", "file": filename, "key": key})
    state = radio.wait_playing(port, filename)
    check(
        int(state.get("cues", 0)) > 0,
        f"the castle plays {name!r} with {state.get('cues')} cues",
    )
    r.post("/radio/device/command", {"action": "stop"})


def run(b: Buyer) -> None:
    card = b.work / "card"
    card.mkdir(parents=True, exist_ok=True)
    emu_port, radio_port = se.free_port(), se.free_port()
    children: list[se.Child] = []
    try:
        children.append(b.castle(emu_port, card))
        children.append(b.launch(radio_port))
        r = radio.Radio(f"http://127.0.0.1:{radio_port}")
        tools = r.get("/radio/tools")
        missing = [c["name"] for c in tools["checks"] if not c["ok"]]
        check(tools["ready"] is True, f"the readiness card is all green {missing}")
        find_castle(r, emu_port)
        jobs = import_songs(b, r)
        send_and_play(b, r, jobs, emu_port, card)
        diagnostics = r.get("/radio/diagnostics")
        (b.logs / "diagnostics.json").write_text(
            json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        check(bool(diagnostics), "Copy diagnostics answers")
    finally:
        for child in reversed(children):
            child.stop()
        b.keep_evidence()


def library_after_reinstall(b: Buyer, rows: list[dict[str, Any]]) -> None:
    """After --uninstall and a reinstall, Castle Radio lists the kept songs."""
    port = se.free_port()
    child = b.launch(port, "castle-radio-reinstalled")
    try:
        library = radio.Radio(f"http://127.0.0.1:{port}").get("/radio/library")
        titles = sorted(row["title"] for row in library)
        check(titles == sorted(row["title"] for row in rows),
              f"the reinstalled Castle Radio lists the kept songs {titles}")  # fmt: skip
    finally:
        child.stop()
