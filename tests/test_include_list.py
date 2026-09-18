"""Every firmware header a target's headers #include must be in its
`esphome: includes:` list — ESPHome copies only the listed files into the
build tree, so a header that is merely #included compiles on the host
harness and dies on the board ("sd_space.h: No such file or directory")."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware"
# Every target is castle.yaml + castle_sd_common.yaml as packages: one include
# list, spelled across the two files that are the show. Neither of the two
# buildable roots (castle_feather_s3.yaml, castle_s3.yaml) adds a header of its
# own, which is why this list is the whole contract.
TARGETS = ("castle.yaml", "castle_sd_common.yaml")


def listed(target: str) -> set[str]:
    """The `includes:` list, read as text: the file carries !include tags
    a plain loader rejects, and the list is one line per header."""
    text = (FW / target).read_text()
    block = re.search(r"^  includes:\n((?:    (?:- |#).*\n)+)", text, re.MULTILINE)
    assert block, f"{target} has no esphome includes list"
    lines = (line.strip() for line in block.group(1).splitlines())
    return {Path(line[2:]).name for line in lines if line.startswith("- ")}


def local_includes(header: Path) -> set[str]:
    return set(re.findall(r'^#include "([^"/]+\.h)"', header.read_text(), re.MULTILINE))


class IncludeListTests(unittest.TestCase):
    def test_every_included_header_is_listed(self) -> None:
        names = set().union(*(listed(t) for t in TARGETS))
        for name in sorted(names):
            header = FW / name
            if not header.exists():
                continue
            # Quoted system headers (esp_system.h) come from IDF, not this dir.
            missing = {m for m in local_includes(header) - names if (FW / m).exists()}
            self.assertFalse(
                missing,
                f"{name} includes {sorted(missing)} but no target lists them",
            )


if __name__ == "__main__":
    unittest.main()
