#!/usr/bin/env python3
"""Talk to the castle over the ESPHome API — list, press, set, watch.

`esphome logs` streams, and `esphome upload` flashes, but neither can press a
button on the device. That matters here more than on a normal ESPHome board:
this one has no serial console, so every diagnostic it can offer has to be
triggered and read over the API.

    tools/device.py 10.27.27.7 list
    tools/device.py 10.27.27.7 press "Dump boot log"
    tools/device.py 10.27.27.7 set "SD file" spooky.mp3
    tools/device.py 10.27.27.7 watch 30

`watch` prints log lines for N seconds, and every command implies a short watch
so the effect of a press comes back on the same invocation — the whole point is
seeing what the button printed.
"""

from __future__ import annotations

import asyncio
import sys

from aioesphomeapi import APIClient, LogLevel


async def connect(host: str, port: int = 6053, password: str = "") -> APIClient:
    cli = APIClient(host, port, password)
    await cli.connect(login=True)
    return cli


def _key_for(entities, name: str):
    """Match on the visible name, case-insensitively, exact before substring."""
    wanted = name.strip().lower()
    for e in entities:
        if getattr(e, "name", "").lower() == wanted:
            return e
    for e in entities:
        if wanted in getattr(e, "name", "").lower():
            return e
    return None


async def run(host: str, cmd: str, args: list[str]) -> int:
    cli = await connect(host)
    try:
        entities, _services = await cli.list_entities_services()

        if cmd == "list":
            for e in sorted(entities, key=lambda x: (type(x).__name__, x.name)):
                print(f"{type(e).__name__:22} {e.name}")
            return 0

        seconds = 8.0
        if cmd == "watch":
            seconds = float(args[0]) if args else 15.0

        # Logs are subscribed BEFORE the press, or the reply races the
        # subscription and the interesting lines never arrive.
        loop = asyncio.get_running_loop()
        done = loop.create_future()

        def on_log(msg) -> None:
            line = msg.message
            print(
                line.decode("utf-8", "replace") if isinstance(line, bytes) else line,
                flush=True,
            )

        # An explicit level is required: without it the device sends nothing,
        # which reads exactly like a button that did not fire.
        cli.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_VERY_VERBOSE)
        await asyncio.sleep(0.4)

        if cmd == "press":
            ent = _key_for(entities, " ".join(args))
            if ent is None:
                print(f"no entity named {' '.join(args)!r}", file=sys.stderr)
                return 2
            cli.button_command(ent.key)
        elif cmd == "set":
            ent = _key_for(entities, args[0])
            if ent is None:
                print(f"no entity named {args[0]!r}", file=sys.stderr)
                return 2
            cli.text_command(ent.key, " ".join(args[1:]))
        elif cmd != "watch":
            print(f"unknown command {cmd!r}", file=sys.stderr)
            return 2

        try:
            await asyncio.wait_for(done, timeout=seconds)
        except asyncio.TimeoutError:
            pass
        return 0
    finally:
        await cli.disconnect()


def main() -> int:
    from hosts import maybe_host

    host, rest = maybe_host(sys.argv[1:])
    if not rest:
        print(__doc__)
        return 2
    cmd, *args = rest
    return asyncio.run(run(host, cmd, args))


if __name__ == "__main__":
    raise SystemExit(main())
