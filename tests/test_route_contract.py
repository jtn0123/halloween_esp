"""One list of castle routes, in four places — held together here (C2).

`/api/events` shipped in firmware 5.59 and was in no allowlist and in no
doc: both relays refuse an unknown `/api/*` path before they open a socket,
so the one endpoint that explains a glitch answered `404 unknown castle
route` from the desk while the castle served it happily. Nothing bound the
lists together — `KNOWN_API` was grepped only by its own two definitions,
and no test read `docs/API.md` at all — so the next route added to `reg()`
would drift exactly the same way.

The four:
  * `firmware/sd_web.h`'s `reg()` table — the truth, parsed by
    tests/firmware_source.py (and already pinned to the emulator by
    tests/test_firmware_contract.py).
  * `tools/castle_link.py`'s `KNOWN_API`/`KNOWN_PREFIX` — the Python relay.
  * `core/src/studio_relay.rs`'s copy of the same two lists.
  * `docs/API.md`'s relayed table — what a human is told.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))  # firmware_source

import castle_link
from firmware_source import firmware_routes

RELAY_RS = ROOT / "core" / "src" / "studio_relay.rs"
API_DOC = ROOT / "docs" / "API.md"

#: Routes the castle serves that a relay deliberately does NOT carry, with
#: the reason. Neither is a `/api/*` route and neither is a client's to ask
#: for through the studio: the studio answers `/` with its own page, and
#: `/site/*` is the castle serving the Castle Radio page off its own card to
#: a browser pointed straight at it.
NOT_RELAYED = {
    "/": "the studio serves its own page at /",
    "/site/*": "the card's own page, read by a browser pointed at the castle",
}


def rust_list(name: str) -> list[str]:
    """One `pub const <name>: [&str; N] = [...]` from studio_relay.rs."""
    body = re.search(
        rf"pub const {name}: \[&str; (\d+)\] = \[(.*?)\];",
        RELAY_RS.read_text(),
        re.DOTALL,
    )
    assert body is not None, f"no {name} in {RELAY_RS}"
    entries = re.findall(r'"([^"]+)"', body.group(2))
    assert len(entries) == int(body.group(1)), (
        f"{name} says [&str; {body.group(1)}] and holds {len(entries)} — "
        "Rust would not compile, but say so here rather than in cargo"
    )
    return entries


def documented_paths() -> set[str]:
    """Every castle path named in docs/API.md's relayed table, normalised
    to the spelling an allowlist uses: the parameters, the `<name>` stand-in
    and the `[?d=…]` optional query all come off."""
    text = API_DOC.read_text()
    # Past the heading itself: it names the `/api/…` family, not a route.
    start = text.index("\n", text.index("## Relayed to the castle"))
    found = set()
    for token in re.findall(r"`(/(?:api|sd|remote)[^`]*)`", text[start:]):
        path = re.split(r"[?\[ ]", token, maxsplit=1)[0]
        # `/api/files/<name>` and `/sd/<path>` are the prefix rules.
        path = re.sub(r"<[^>]*>$", "", path)
        found.add(path)
    return found


def allowed(path: str, api: list[str], prefixes: list[str]) -> bool:
    return path in api or any(path.startswith(p) for p in prefixes)


class TestTheRelaysCarryEveryRouteTheCastleServes(unittest.TestCase):
    def test_both_allowlists_cover_the_firmwares_own_table(self) -> None:
        api, prefixes = list(castle_link.KNOWN_API), list(castle_link.KNOWN_PREFIX)
        for path, method, handler in firmware_routes():
            if path in NOT_RELAYED:
                continue
            # A wildcard route is a prefix on both sides: `/api/files/*`.
            probe = path.removesuffix("*")
            self.assertTrue(
                allowed(probe, api, prefixes),
                f"{method} {path} ({handler}) is served by the firmware and "
                f"refused by castle_link — add it to KNOWN_API/KNOWN_PREFIX "
                f"or to NOT_RELAYED with the reason",
            )

    def test_the_two_relays_hold_the_same_two_lists(self) -> None:
        """The Rust relay is a second copy, not a second opinion."""
        self.assertEqual(rust_list("KNOWN_API"), list(castle_link.KNOWN_API))
        self.assertEqual(rust_list("KNOWN_PREFIX"), list(castle_link.KNOWN_PREFIX))

    def test_nothing_is_allowed_that_the_firmware_does_not_serve(self) -> None:
        """The drift in the other direction: an allowlist that outlives a
        route lets a client's typo reach the castle and come back a 404 from
        the board, which reads as an outage rather than a typo."""
        # A wildcard and its prefix are the same route with two spellings
        # (`/api/files/*` in reg(), `/api/files/` in the allowlist), so both
        # sides lose their trailing markers before they are compared.
        served = {p.rstrip("*").rstrip("/") for p, _m, _h in firmware_routes()}
        for path in [*castle_link.KNOWN_API, *castle_link.KNOWN_PREFIX]:
            self.assertIn(
                path.rstrip("/"),
                served,
                f"{path} is relayed and the firmware's reg() table has no such route",
            )


class TestTheDocSaysWhatTheRelaysDo(unittest.TestCase):
    """docs/API.md is the only one of the four a person reads, and until now
    no test read it at all."""

    def test_every_allowed_route_is_in_the_doc(self) -> None:
        documented = documented_paths()
        for path in [*castle_link.KNOWN_API, *castle_link.KNOWN_PREFIX]:
            self.assertIn(
                path,
                documented,
                f"{path} is relayed and docs/API.md's table does not mention it",
            )

    def test_the_doc_promises_nothing_the_relays_refuse(self) -> None:
        api, prefixes = list(castle_link.KNOWN_API), list(castle_link.KNOWN_PREFIX)
        for path in documented_paths():
            self.assertTrue(
                allowed(path, api, prefixes),
                f"docs/API.md names {path}, which the relay's allowlist refuses",
            )


if __name__ == "__main__":
    unittest.main()
