"""tools/qr_code.py — the label's QR encoder, held to the standard and to an
independent decoder's verdict.

Three kinds of evidence, because an encoder that agrees only with itself
proves nothing:

  * the standard's own numbers — ISO/IEC 18004 Table 7's byte capacities,
    Annex I's worked Reed-Solomon example, the format and version words of
    Annexes C and D, Annex E's alignment centres;
  * two symbols pinned module for module (GOLDEN). Both were read back by
    zxing-cpp 3.1.1 — a decoder sharing no code with this one — when they
    were pinned (2026-10-03), as were 960 others across every version,
    level and mask; a change that moves a module here must be read back the
    same way before the golden is replaced;
  * a reader written here from the standard, run over every version: it
    finds the format word, unmasks, walks the zigzag, de-interleaves the
    blocks, demands every block's Reed-Solomon syndromes be zero, and parses
    the byte segment back to the input.
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import qr_code as q

GUIDE = b"https://github.com/jtn0123/halloween_esp/blob/main/docs/OWNER-GUIDE.md"
STICKER = b"Castle-C3D4 castle-b2c3d4.local " * 4
#: (data, level) -> (version, mask, rows as hex), read back by zxing-cpp.
GOLDEN = {
    (GUIDE, "M"): (5, 6, (
        "1fd29bde7f 1055628541 175932cc5d 174399c95d 1751965d5d 104701ad41 1fd555557f "
        "0003fc1f00 13f2821c97 13bcd44c36 1ef43b3b6d 07959b2637 1bd7706169 188e74be18 "
        "05c4279fdf 0a2ce2136e 18fd0e7244 088fc0ec88 0744522aa1 04b1375bc9 0ceb5221f8 "
        "07ac3a7694 0f67e1ff29 162915403f 1345a9d760 1a060e861c 18cf68c6c7 178a12434c "
        "1272b37bfd 00196fcb16 1fda2f3951 1056a28b10 175e758ff1 175f414dc5 174248af91 "
        "1043e802df 1fd2432129")),
    (STICKER, "Q"): (9, 5, (
        "1fdaf79e7fc47f 1058e8a627c641 174fabe094925d 1745e5f31b5d5d 17477abf812c5d "
        "1045075176e441 1fd5555555557f 0001b4d1703800 0874249fa5e583 06044e4cfd6fdd "
        "18d89db58fe0f0 19bb25f5509708 0be988bad5da9b 103b7603e91eb1 0a5406444e53e0 "
        "091d6715a4d96d 04f63505690570 02ad2dd2e93cbc 0a6ee7d7b9393d 178ccef35cf6c8 "
        "1e771115e72916 090aebc07ed7f4 0c7edb72cd49fa 0d12b24bf0082a 0dfdee5f1d17f1 "
        "011631d129af1d 0d5ce4754d1758 0919a79118cd1e 1ffb227f4705f6 16132eb48935f1 "
        "0479ba1bf8b251 1400d896720ed8 10f8335169cc44 04a1a5b6edf7d8 0d69e67d0f1032 "
        "0daa7ac8109778 14c1fd76d1d3c8 0e003f5ea835f3 17444e5e4e693c 0426872bc65f9e "
        "03f41d0521cca6 1131cd23f9a65d 1bdb0329f1cbef 0c2a8e4ddcd7d8 027138ffeb69f5 "
        "0016a11124d516 1fdb9eb58e495c 104307b112bf1b 174ef4dfd3f7fb 17487cee7937e8 "
        "174402850f60bb 1051d9c48486e5 1fc9645fe7c8d4")),
}  # fmt: skip


def as_hex(code: q.Code) -> str:
    width = (code.size + 3) // 4
    return " ".join(
        format(int("".join("1" if d else "0" for d in row), 2), f"0{width}x")
        for row in code.modules
    )


def gf_pow(base: int, n: int) -> int:
    out = 1
    for _ in range(n):
        out = q.gf_mul(out, base)
    return out


def syndromes(block: list[int], ecc: int) -> list[int]:
    """block(x) at a^0 .. a^(ecc-1): all zero for a valid codeword."""
    out = []
    for j in range(ecc):
        x, acc = gf_pow(2, j), 0
        for c in block:
            acc = q.gf_mul(acc, x) ^ c
        out.append(acc)
    return out


def read(code: q.Code) -> tuple[str, int, bytes]:
    """Decode `code` from its modules: (level, mask, payload)."""
    m, n = code.modules, code.size
    word = sum(int(m[i][8]) << i for i in range(6))
    word |= int(m[7][8]) << 6 | int(m[8][8]) << 7 | int(m[8][7]) << 8
    word |= sum(int(m[8][14 - i]) << i for i in range(9, 15))
    second = sum(int(m[8][n - 1 - i]) << i for i in range(8))
    second |= sum(int(m[n - 15 + i][8]) << i for i in range(8, 15))
    assert word == second, "the two format copies disagree"
    level, mask = next(
        (lv, mk) for lv in q.LEVELS for mk in range(8) if q.format_bits(lv, mk) == word
    )
    version = (n - 17) // 4
    grid = q._Grid(version)
    grid.functions()
    bits: list[bool] = []
    right = n - 1
    while right >= 1:
        right = 5 if right == 6 else right
        upward = ((right + 1) & 2) == 0
        for v in range(n):
            r = n - 1 - v if upward else v
            bits.extend(
                m[r][c] != q.MASKS[mask](r, c)
                for c in (right, right - 1)
                if not grid.fixed[r][c]
            )
        right -= 2
    words = [
        int("".join("1" if b else "0" for b in bits[i : i + 8]), 2)
        for i in range(0, len(bits) // 8 * 8, 8)
    ]
    blocks, ecc = q.BLOCKS[level][version], q.ECC_PER_BLOCK[level][version]
    total = q.raw_modules(version) // 8
    short = blocks - total % blocks
    lens = [total // blocks - ecc + (b >= short) for b in range(blocks)]
    data: list[list[int]] = [[] for _ in range(blocks)]
    at = 0
    for i in range(max(lens)):
        for b in range(blocks):
            if i < lens[b]:
                data[b].append(words[at])
                at += 1
    for b in range(blocks):
        tail = [words[at + i * blocks + b] for i in range(ecc)]
        assert syndromes(data[b] + tail, ecc) == [0] * ecc, f"block {b} corrupt"
    stream = "".join(format(w, "08b") for part in data for w in part)
    assert stream[:4] == "0100", "not a byte segment"
    width = 8 if version < 10 else 16
    count = int(stream[4 : 4 + width], 2)
    body = stream[4 + width : 4 + width + 8 * count]
    return level, mask, bytes(int(body[i : i + 8], 2) for i in range(0, len(body), 8))


class TestTheStandardsNumbers(unittest.TestCase):
    def test_byte_capacities_are_table_7s(self) -> None:
        table = {
            1: (17, 14, 11, 7),
            2: (32, 26, 20, 14),
            5: (106, 84, 60, 44),
            7: (154, 122, 86, 64),
            10: (271, 213, 151, 119),
            27: (1465, 1125, 805, 625),
            40: (2953, 2331, 1663, 1273),
        }
        for version, caps in table.items():
            got = tuple(q.byte_capacity(version, lv) for lv in q.LEVELS)
            self.assertEqual(got, caps, version)

    def test_every_version_spends_exactly_its_codewords(self) -> None:
        self.assertEqual(
            [q.raw_modules(v) // 8 for v in (1, 5, 10, 40)], [26, 134, 346, 3706]
        )
        for v in range(1, 41):
            for lv in q.LEVELS:
                spent = (
                    q.data_codewords(v, lv) + q.ECC_PER_BLOCK[lv][v] * q.BLOCKS[lv][v]
                )
                self.assertEqual(spent, q.raw_modules(v) // 8, (v, lv))

    def test_reed_solomon_matches_the_worked_examples(self) -> None:
        # ISO/IEC 18004 Annex I: "01234567" at 1-M.
        annex = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80] + [0xEC, 0x11] * 5
        self.assertEqual(
            q.rs_remainder(annex, q.rs_generator(10)),
            [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55],
        )
        # "HELLO WORLD" at 1-M, the classic tutorial's.
        hello = [0x20, 0x5B, 0x0B, 0x78, 0xD1, 0x72, 0xDC, 0x4D, 0x43, 0x40]
        self.assertEqual(
            q.rs_remainder(hello + [0xEC, 0x11] * 3, q.rs_generator(10)),
            [0xC4, 0x23, 0x27, 0x77, 0xEB, 0xD7, 0xE7, 0xE2, 0x5D, 0x17],
        )

    def test_format_and_version_words_are_the_annexes(self) -> None:
        self.assertEqual(q.format_bits("M", 0), 0b101010000010010)
        self.assertEqual(q.format_bits("L", 0), 0b111011111000100)
        self.assertEqual(q.format_bits("H", 7), 0b000100000111011)
        self.assertEqual(q.version_bits(7), 0b000111110010010100)
        self.assertEqual(q.version_bits(40), 0b101000110001101001)

    def test_alignment_centres_are_annex_es(self) -> None:
        self.assertEqual(q.alignment_centres(1), [])
        self.assertEqual(q.alignment_centres(2), [6, 18])
        self.assertEqual(q.alignment_centres(7), [6, 22, 38])
        self.assertEqual(q.alignment_centres(32), [6, 34, 60, 86, 112, 138])
        self.assertEqual(q.alignment_centres(40), [6, 30, 58, 86, 114, 142, 170])


class TestSymbols(unittest.TestCase):
    def test_the_pinned_symbols_are_unchanged(self) -> None:
        for (data, level), (version, mask, rows) in GOLDEN.items():
            code = q.encode(data, level)
            self.assertEqual((code.version, code.mask), (version, mask))
            self.assertEqual(as_hex(code), rows)

    def test_every_version_and_level_reads_back(self) -> None:
        rng = random.Random(18004)
        for version in range(1, 41):
            for level in q.LEVELS:
                cap = q.byte_capacity(version, level)
                data = bytes(rng.randrange(256) for _ in range(rng.randint(1, cap)))
                mask = rng.randrange(8)
                code = q.encode(data, level, version, mask)
                self.assertEqual(code.size, 17 + 4 * version)
                self.assertEqual(read(code), (level, mask, data), (version, level))

    def test_the_mask_chosen_is_the_lowest_penalty(self) -> None:
        code = q.encode(GUIDE, "M")
        scores = [q.penalty([list(r) for r in q.encode(GUIDE, "M", 5, m).modules])
                  for m in range(8)]  # fmt: skip
        self.assertEqual(scores[code.mask], min(scores))
        self.assertEqual(code.mask, scores.index(min(scores)))

    def test_the_smallest_version_that_holds_it(self) -> None:
        self.assertEqual(q.encode(b"x" * 14, "M").version, 1)
        self.assertEqual(q.encode(b"x" * 15, "M").version, 2)
        self.assertEqual(q.encode(b"x" * 2953, "L").version, 40)

    def test_what_cannot_be_encoded_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "do not fit a QR code"):
            q.encode(b"x" * 2954, "L")
        with self.assertRaisesRegex(ValueError, "do not fit version 1-H"):
            q.encode(b"x" * 8, "H", version=1)
        with self.assertRaisesRegex(ValueError, "do not fit version 41"):
            q.encode(b"x", "L", version=41)
        with self.assertRaisesRegex(ValueError, "level must be one of"):
            q.encode(b"x", "X")


class TestSvg(unittest.TestCase):
    def test_one_square_per_dark_module_inside_the_quiet_zone(self) -> None:
        code = q.encode(GUIDE, "M")
        out = q.svg(code, module=2, quiet=4, label="Owner's guide")
        side = code.size + 8
        self.assertIn(f'viewBox="0 0 {side} {side}"', out)
        self.assertIn(f'width="{side * 2}"', out)
        self.assertIn("<title>Owner's guide</title>", out)
        dark = sum(map(sum, code.modules))
        self.assertEqual(out.count("h1v1h-1z"), dark)
        self.assertIn("M4 4h1v1h-1z", out)  # the finder's corner, past the zone
        self.assertNotIn("<title>", q.svg(code))


if __name__ == "__main__":
    unittest.main()
