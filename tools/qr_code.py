"""A QR code encoder — byte mode, every version, standard library only.

The unit label (tools/unit_label.py) carries one QR code: the owner's guide
on GitHub. That is one short URL, printed once per castle, so this is the
smallest encoder that does it properly rather than a dependency: ISO/IEC
18004 model 2, byte mode only (a URL is bytes), versions 1-40, the four
error-correction levels, Reed-Solomon over GF(256), and the mask chosen by
the standard's four penalty rules. No Kanji, no numeric/alphanumeric
segments, no ECI, no Micro QR — none of which a URL on a label needs.

    encode(b"https://...", "M")   -> Code: version, level, mask, modules
    svg(code)                     -> one <svg> element, quiet zone included

tests/test_qr_code.py holds it to an independent encoder's output, module
for module, and to the standard's own arithmetic (capacities, BCH words,
every block's Reed-Solomon syndromes).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise

LEVELS = "LMQH"
#: The two format-information bits each level is written as (Table 12).
LEVEL_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}

# Table 9, by level then version (index 0 unused): error-correction
# codewords in each block, and how many blocks the codewords split into.
ECC_PER_BLOCK = {
    "L": (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
          28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
          26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    "Q": (0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
          28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "H": (0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
          30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}  # fmt: skip
BLOCKS = {
    "L": (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
          8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    "M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
          17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    "Q": (0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
          23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    "H": (0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
          25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}  # fmt: skip

#: The penalty weights of §8.8.2: runs, 2x2 blocks, finder look-alikes, balance.
N1, N2, N3, N4 = 3, 3, 40, 10
_FINDER_LIKE = ((1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0), (0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1))

MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


@dataclass(frozen=True)
class Code:
    """One finished symbol: modules[row][col], True = dark."""

    version: int
    level: str
    mask: int
    modules: tuple[tuple[bool, ...], ...]

    @property
    def size(self) -> int:
        return len(self.modules)


# ── GF(256) and Reed-Solomon ───────────────────────────────────────────────


def gf_mul(a: int, b: int) -> int:
    """Product in GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1 (0x11D)."""
    out = 0
    while b:
        if b & 1:
            out ^= a
        b >>= 1
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
    return out


def rs_generator(degree: int) -> list[int]:
    """(x - a^0)(x - a^1)...(x - a^(degree-1)), highest power first, its
    leading 1 dropped."""
    poly = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            poly[j] = gf_mul(poly[j], root)
            if j + 1 < degree:
                poly[j] ^= poly[j + 1]
        root = gf_mul(root, 2)
    return poly


def rs_remainder(data: list[int], generator: list[int]) -> list[int]:
    """The error-correction codewords: data(x) * x^n mod generator(x)."""
    out = [0] * len(generator)
    for byte in data:
        factor = byte ^ out.pop(0)
        out.append(0)
        for i, coef in enumerate(generator):
            out[i] ^= gf_mul(coef, factor)
    return out


# ── Capacity ───────────────────────────────────────────────────────────────


def size_of(version: int) -> int:
    return 17 + 4 * version


def alignment_centres(version: int) -> list[int]:
    """Row/column centres of the alignment patterns (Annex E)."""
    if version == 1:
        return []
    count = version // 7 + 2
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    return [6, *sorted(size_of(version) - 7 - i * step for i in range(count - 1))]


def raw_modules(version: int) -> int:
    """Modules left for codewords once every function pattern is drawn."""
    n = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        n -= (25 * count - 10) * count - 55
        if version >= 7:
            n -= 36
    return n


def data_codewords(version: int, level: str) -> int:
    return (
        raw_modules(version) // 8
        - ECC_PER_BLOCK[level][version] * BLOCKS[level][version]
    )


def byte_capacity(version: int, level: str) -> int:
    """How many bytes fit: 4 mode bits and an 8- or 16-bit count go first."""
    count_bits = 8 if version < 10 else 16
    return (data_codewords(version, level) * 8 - 4 - count_bits) // 8


def smallest_version(length: int, level: str) -> int:
    for version in range(1, 41):
        if length <= byte_capacity(version, level):
            return version
    raise ValueError(f"{length} bytes do not fit a QR code at level {level}")


# ── Codewords ──────────────────────────────────────────────────────────────


def data_bits(data: bytes, version: int, level: str) -> list[int]:
    """Mode, count, the bytes, the terminator and the pad: codewords."""
    bits: list[int] = []

    def put(value: int, width: int) -> None:
        bits.extend((value >> i) & 1 for i in range(width - 1, -1, -1))

    put(0b0100, 4)
    put(len(data), 8 if version < 10 else 16)
    for byte in data:
        put(byte, 8)
    room = data_codewords(version, level) * 8
    put(0, min(4, room - len(bits)))
    put(0, -len(bits) % 8)
    words = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    words += [pad[i % 2] for i in range(room // 8 - len(words))]
    return words


def interleave(words: list[int], version: int, level: str) -> list[int]:
    """Split into blocks, add each block's ECC, interleave (§7.6)."""
    blocks = BLOCKS[level][version]
    ecc = ECC_PER_BLOCK[level][version]
    total = raw_modules(version) // 8
    short = blocks - total % blocks
    short_len = total // blocks - ecc
    gen = rs_generator(ecc)
    data_parts, ecc_parts = [], []
    at = 0
    for b in range(blocks):
        n = short_len + (b >= short)
        part = words[at : at + n]
        at += n
        data_parts.append(part)
        ecc_parts.append(rs_remainder(part, gen))
    out = []
    for i in range(short_len + 1):
        out += [part[i] for part in data_parts if i < len(part)]
    for i in range(ecc):
        out += [part[i] for part in ecc_parts]
    return out


# ── The matrix ─────────────────────────────────────────────────────────────


def format_bits(level: str, mask: int) -> int:
    """15 bits: level and mask, BCH(15,5), XOR 0x5412 (§7.9)."""
    data = LEVEL_BITS[level] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def version_bits(version: int) -> int:
    """18 bits: the version and its BCH(18,6) remainder (§7.10)."""
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return version << 12 | rem


class _Grid:
    def __init__(self, version: int) -> None:
        self.version = version
        self.n = size_of(version)
        self.dark = [[False] * self.n for _ in range(self.n)]
        self.fixed = [[False] * self.n for _ in range(self.n)]

    def put(self, r: int, c: int, dark: bool) -> None:
        self.dark[r][c] = dark
        self.fixed[r][c] = True

    def functions(self) -> None:
        self._timing()
        self._finders()
        self._alignments()
        self.format(0)  # reserve, with any value
        if self.version >= 7:
            self._version_info()

    def _timing(self) -> None:
        for i in range(self.n):
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)

    def _finders(self) -> None:
        """The three finders with their separators: rings 2 and 4 light."""
        n = self.n
        for r0, c0 in ((3, 3), (3, n - 4), (n - 4, 3)):
            for dr in range(-4, 5):
                for dc in range(-4, 5):
                    r, c = r0 + dr, c0 + dc
                    if 0 <= r < n and 0 <= c < n:
                        self.put(r, c, max(abs(dr), abs(dc)) not in (2, 4))

    def _alignments(self) -> None:
        centres = alignment_centres(self.version)
        last = len(centres) - 1
        under_finders = {(0, 0), (0, last), (last, 0)}
        for i, r0 in enumerate(centres):
            for j, c0 in enumerate(centres):
                if (i, j) not in under_finders:
                    self._alignment(r0, c0)

    def _alignment(self, r0: int, c0: int) -> None:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                self.put(r0 + dr, c0 + dc, max(abs(dr), abs(dc)) != 1)

    def _version_info(self) -> None:
        bits = version_bits(self.version)
        for i in range(18):
            dark = ((bits >> i) & 1) == 1
            a, b = self.n - 11 + i % 3, i // 3
            self.put(a, b, dark)
            self.put(b, a, dark)

    def format(self, bits: int) -> None:
        n = self.n

        def bit(i: int) -> bool:
            return ((bits >> i) & 1) == 1

        for i in range(6):
            self.put(i, 8, bit(i))
        self.put(7, 8, bit(6))
        self.put(8, 8, bit(7))
        self.put(8, 7, bit(8))
        for i in range(9, 15):
            self.put(8, 14 - i, bit(i))
        for i in range(8):
            self.put(8, n - 1 - i, bit(i))
        for i in range(8, 15):
            self.put(n - 15 + i, 8, bit(i))
        self.put(n - 8, 8, True)  # the dark module

    def place(self, codewords: list[int]) -> None:
        bits = [((w >> (7 - i)) & 1) == 1 for w in codewords for i in range(8)]
        for k, (r, c) in enumerate(self._zigzag()):
            self.dark[r][c] = bits[k] if k < len(bits) else False

    def _zigzag(self) -> Iterator[tuple[int, int]]:
        """The data modules in the order of §7.7.3: column pairs from the
        right, skipping the vertical timing column, alternately upwards and
        downwards."""
        n = self.n
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for v in range(n):
                r = n - 1 - v if upward else v
                yield from ((r, c) for c in (right, right - 1) if not self.fixed[r][c])
            right -= 2

    def masked(self, mask: int) -> list[list[bool]]:
        test = MASKS[mask]
        return [
            [d != (not self.fixed[r][c] and test(r, c)) for c, d in enumerate(row)]
            for r, row in enumerate(self.dark)
        ]


def _runs_penalty(line: list[bool]) -> int:
    score, run = 0, 1
    for a, b in pairwise(line):
        if a == b:
            run += 1
            continue
        if run >= 5:
            score += N1 + run - 5
        run = 1
    return score + (N1 + run - 5 if run >= 5 else 0)


def _finder_penalty(line: list[bool]) -> int:
    bits = tuple(int(x) for x in line)
    return N3 * sum(bits[i : i + 11] in _FINDER_LIKE for i in range(len(bits) - 10))


def penalty(m: list[list[bool]]) -> int:
    """§8.8.2's four rules, summed over rows and columns."""
    n = len(m)
    cols = [[m[r][c] for r in range(n)] for c in range(n)]
    score = sum(_runs_penalty(line) + _finder_penalty(line) for line in m + cols)
    score += N2 * sum(
        m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]
        for r in range(n - 1)
        for c in range(n - 1)
    )
    dark = sum(map(sum, m))
    score += N4 * (abs(dark * 20 - n * n * 10) // (n * n))
    return score


def encode(
    data: bytes, level: str = "M", version: int | None = None, mask: int | None = None
) -> Code:
    """The symbol for `data`: the smallest version that holds it at `level`
    (or `version`), with the lowest-penalty mask (or `mask`)."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}, not {level!r}")
    if version is None:
        version = smallest_version(len(data), level)
    elif not 1 <= version <= 40 or len(data) > byte_capacity(version, level):
        raise ValueError(f"{len(data)} bytes do not fit version {version}-{level}")
    grid = _Grid(version)
    grid.functions()
    grid.place(interleave(data_bits(data, version, level), version, level))
    best: tuple[int, int, list[list[bool]]] | None = None
    for m in range(8) if mask is None else (mask,):
        grid.format(format_bits(level, m))
        candidate = grid.masked(m)
        score = penalty(candidate) if mask is None else 0
        if best is None or score < best[0]:
            best = (score, m, candidate)
    assert best is not None
    rows = tuple(tuple(row) for row in best[2])
    return Code(version, level, best[1], rows)


def svg(code: Code, module: float = 1.0, quiet: int = 4, label: str = "") -> str:
    """The symbol as one inline <svg>, `quiet` light modules round it, one
    path of dark squares — crisp at any print size."""
    side = code.size + 2 * quiet
    path = "".join(
        f"M{c + quiet} {r + quiet}h1v1h-1z"
        for r, row in enumerate(code.modules)
        for c, dark in enumerate(row)
        if dark
    )
    title = f"<title>{label}</title>" if label else ""
    px = side * module
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" '
        f'width="{px:g}" height="{px:g}" shape-rendering="crispEdges" role="img">'
        f'{title}<rect width="{side}" height="{side}" fill="#fff"/>'
        f'<path d="{path}" fill="#000"/></svg>'
    )
