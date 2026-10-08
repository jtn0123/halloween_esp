"""Find my castle — the Your castle page's routes (castle-find.js).

    GET  /radio/device/address   which castle this app talks to, and why
    POST /radio/device/find      browse the LAN (tools/castle_find.py)
    POST /radio/device/address   {host, name?}: make it THE castle

The browse is a POST, like every route here that does something: a page in
another tab cannot set off a LAN-wide query with an <img>. Adopting asks the
address first — only something that answers /api/status like a castle is
written to the store (castle_place.adopt → tools/castle_address.py) — so a
typo is said, not saved.
"""

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import castle_find
import castle_keys
import castle_place
import device_bridge

NOT_A_CASTLE = (
    "Nothing at {host} answered as a castle — check the address, and that "
    "the castle is switched on and on this network"
)


def where():
    """What the page says about the address, before and after a find."""
    return {
        "host": device_bridge.HOST,
        "pinned": castle_place.pinned(),
        "store": castle_place.has_store(),
    }


def get_address(handler, _parsed):
    handler.reply(where())


def post_find(handler):
    handler.json_body("Invalid find request")
    try:
        found = castle_find.find()
    except OSError as exc:
        raise castle_keys.Refusal(str(exc), 502) from None
    here = device_bridge.HOST
    rows = [{**castle, "current": castle["address"] == here} for castle in found]
    handler.reply({**where(), "found": rows})


def post_address(handler):
    body = handler.json_body("Invalid castle address")
    host = str(body.get("host") or "").strip()
    name = str(body.get("name") or "").strip()
    castle_keys._check_host(host)
    if name:
        castle_keys._check_host(name)
    if castle_place.pinned():
        raise castle_keys.Refusal(castle_place.PINNED, 409)
    state = castle_find.probe(host)
    if state is None:
        raise castle_keys.Refusal(NOT_A_CASTLE.format(host=host), 502)
    device_bridge.HOST = castle_place.adopt(host, name)
    with device_bridge._STATUS_LOCK:
        device_bridge._status_cache["state"] = None
    handler.reply({**where(), "name": name, "version": state.get("version", "")})


GET_ROUTES = {"/radio/device/address": get_address}
POST_ROUTES = {
    "/radio/device/find": post_find,
    "/radio/device/address": post_address,
}
