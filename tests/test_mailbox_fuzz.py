"""The one-slot mailbox and the event ring, fuzzed: the real C against the
emulator's port, one random step at a time.

tests/cxx/events_check.cpp checks the rules someone wrote down. This draws
thousands of set_pending / take_pending / note_light_evictions /
record_event steps with a seeded `random.Random`, runs them through
firmware/sd_web_state.h compiled for the host (tests/cxx/mailbox_fuzz_check.cpp)
and through the model — tools/castle_emu_events.py for the ring and
counters, and the mailbox rule as tools/castle_emu.py states it — and
demands the same answer at every take and the same JSON at every dump.

What that pins that the named cases cannot: every interleaving of the
eviction rules (LIGHT never evicts a non-LIGHT, LIGHT over LIGHT counts,
RESTART is a latch drained first), the one-a-second limit on light_evicted
lines with an arbitrary tick spacing, the 47-byte arg cut, and the
wraparound at 64 under any mix of kinds.

Args are drawn from what the handlers can actually queue — safe_name'd
ASCII, digits, the show flags — plus the bytes json_escape must escape.
Non-ASCII is left out on purpose: no route can queue it, and the two sides
would legitimately differ on a multibyte character split at byte 47.

Knobs: CASTLE_MAILBOX_SEED (default 1), CASTLE_MAILBOX_ROUNDS (default 40).
Every failure names the seed and the step so it replays.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from castle_emu_events import ARG_MAX, RING, Events
from firmware_web_harness import COMPILER, CXX_DIR, FIRMWARE, IN_CI

SEED = int(os.environ.get("CASTLE_MAILBOX_SEED", "1"))
ROUNDS = int(os.environ.get("CASTLE_MAILBOX_ROUNDS", "40"))

#: sd_web_state.h ActionType, by value.
ACTIONS = {
    1: "PLAY",
    2: "SCENE",
    3: "STOP",
    4: "VOLUME",
    5: "LIGHT",
    6: "PIRCFG",
    7: "RESTART",
    8: "SHOW",
    9: "BLACKOUT",
}
#: EventKind, by value — the two the main loop records outside record_action.
RAW_KINDS = {8: "sound", 9: "silent"}

WORDS = ["vigil", "storm", "stop", "seance", "0", "1", "45", "100", "", "radio_a.mp3"]
SPICE = '"\\\b\f\n\r\t\x01\x1f\x7f'


def draw_arg(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.5:
        return rng.choice(WORDS)
    if kind < 0.8:
        n = rng.choice([1, 8, ARG_MAX - 1, ARG_MAX, ARG_MAX + 1, 80, 200])
        return "".join(rng.choice("abcxyz_-.0123456789") for _ in range(n))
    n = rng.randint(1, 60)
    return "".join(rng.choice("ab" + SPICE) for _ in range(n))


class Model:
    """set_pending / take_pending as tools/castle_emu.py states them, over
    the emulator's own ring and counters."""

    def __init__(self) -> None:
        self.events = Events()
        self.pending: tuple[str, str] | None = None
        self.restart = False

    def set(self, action: str, arg: str) -> None:
        if action == "RESTART":
            self.restart = True
            return
        if (
            action == "LIGHT"
            and self.pending is not None
            and self.pending[0] != "LIGHT"
        ):
            return
        if action == "LIGHT" and self.pending is not None:
            self.events.light_evicted += 1
        self.pending = (action, arg)

    def take(self, now_us: int) -> tuple[str, str]:
        if self.restart:
            self.restart = False
            taken: tuple[str, str] = ("RESTART", "")
        else:
            taken, self.pending = self.pending or ("NONE", ""), None
        if taken[0] != "NONE":
            self.events.record_action(taken[0], taken[1], now_us // 1000)
        return taken


def build() -> Path:
    exe = Path(tempfile.mkdtemp(prefix="mailbox-fuzz-")) / "mailbox_fuzz_check"
    built = subprocess.run(
        [
            COMPILER or "c++",
            "-std=c++17",
            "-O1",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CXX_DIR / "shim"),
            "-I",
            str(FIRMWARE),
            str(CXX_DIR / "mailbox_fuzz_check.cpp"),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        raise AssertionError(built.stderr)
    return exe


class Board:
    """One process of the compiled C, spoken to line by line."""

    def __init__(self, exe: Path) -> None:
        self.proc = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.inp, self.out = self.proc.stdin, self.proc.stdout

    def send(self, line: str) -> None:
        self.inp.write((line + "\n").encode())
        self.inp.flush()

    def take(self, now_us: int) -> tuple[str, str]:
        self.send(f"T {now_us}")
        word, code, hexarg = self.out.readline().decode().split(" ")
        assert word == "taken", word
        return ACTIONS.get(int(code), "NONE"), bytes.fromhex(hexarg.strip()).decode()

    def dump(self) -> tuple[tuple[int, int], bytes]:
        self.send("D")
        _, applied, evicted = self.out.readline().decode().split()
        _, size = self.out.readline().decode().split()
        return (int(applied), int(evicted)), self.out.read(int(size))

    def close(self) -> str:
        self.send("Q")
        _, err = self.proc.communicate(timeout=10)
        return err.decode()


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestMailboxFuzz(unittest.TestCase):
    exe: Path

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        cls.exe = build()

    def one_round(self, seed: int) -> None:
        rng = random.Random(seed)
        board, model = Board(self.exe), Model()
        now_us = rng.randint(0, 10**9)
        steps = rng.randint(50, 400)
        # A tick every 200 ms as on the board, or jittered, or bunched: the
        # rate limit and the ring order must hold under any spacing.
        gap = rng.choice([200_000, 50_000, 1_000_000, None])
        try:
            for step in range(steps):
                now_us += gap if gap is not None else rng.randint(0, 1_500_000)
                where = f"seed {seed} step {step}"
                r = rng.random()
                if r < 0.55:
                    code = rng.choice([1, 2, 3, 4, 5, 5, 5, 5, 6, 7, 8, 9])
                    arg = draw_arg(rng)
                    board.send(f"S {code} {arg.encode().hex()}")
                    model.set(ACTIONS[code], arg)
                elif r < 0.85:
                    board.send(f"N {now_us}")
                    model.events.note_light_evictions(now_us // 1000)
                    self.assertEqual(board.take(now_us), model.take(now_us), where)
                elif r < 0.95:
                    code = rng.choice(list(RAW_KINDS))
                    board.send(f"E {code} {now_us}")
                    model.events.record(RAW_KINDS[code], "", now_us // 1000)
                else:
                    self.check_dump(board, model, where)
            self.check_dump(board, model, f"seed {seed} final")
        finally:
            err = board.close()
        self.assertEqual(err, "", f"seed {seed}: {err}")

    def check_dump(self, board: Board, model: Model, where: str) -> None:
        counters, body = board.dump()
        self.assertEqual(
            counters, (model.events.light_applied, model.events.light_evicted), where
        )
        self.assertEqual(body.decode(), model.events.json(), where)
        # And what any page would do with it: parse it, oldest first.
        events = json.loads(body)
        self.assertLessEqual(len(events), RING, where)
        self.assertEqual(
            [e["t"] for e in events], sorted(e["t"] for e in events), where
        )
        for e in events:
            self.assertLessEqual(len(e["a"].encode()), ARG_MAX, where)

    def test_the_c_and_the_model_agree_on_every_random_step(self) -> None:
        for seed in range(SEED, SEED + ROUNDS):
            with self.subTest(seed=seed):
                self.one_round(seed)


if __name__ == "__main__":
    unittest.main()
