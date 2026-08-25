# Security — the accepted position

This project is a **single-user tool on a private network**: one person, one
Mac, one castle, one porch LAN. Every trust decision below follows from that,
and every one of them is invalid the moment any part of it is exposed beyond
the LAN. That is the one line not to cross: **do not port-forward, reverse-proxy
or otherwise publish the studio (default port 8820) or the castle (port 80)** —
nothing here is hardened for strangers.

## Accepted risks — decided 2026-08-16, permanently

Recorded so future audits (human or otherwise) re-read the decision instead of
re-raising the finding.

**The studio validates neither `Origin` nor `Host`.** A malicious page open in
the operator's own browser could drive the studio (import tracks, rewrite
scenes, restart the server). Accepted: the studio binds a local machine on a
private network, its state is a hobby show that regenerates from
`scenes/scenes.yaml`, and the operator is the only user. Won't fix.

**The castle's OTA and file endpoints have no authentication.** Anyone on the
LAN can flash firmware or rewrite the SD card. Accepted: physical access to the
porch already grants more, the device is a decoration, and the LAN is the
household's own. Won't fix.

## What IS defended, and why

The defences that exist are for *mistakes*, not adversaries — with one
exception for guests:

- **`tools/netguard.py`** — a pasted import URL that resolves to a private,
  loopback or link-local address is refused unless the caller is the studio's
  own machine. A LAN guest poking at `--lan` must not get a free proxy into
  addresses only this machine can reach (the router's admin page, the castle).
  This is the one adversarial defence, and it is defence in depth only.
- **`scrub()` in `tools/studio_http.py`** — control characters in anything a
  request supplies are escaped before logging, so a crafted URL cannot forge a
  log line.
- **`MAX_BODY`** — request bodies are bounded, and an oversized one drops the
  connection so its unread bytes are not parsed as the next request.
- **Track ids** — letters, digits and underscore, every spelling, after an
  explicit id of `../../audio/01_vigil` walked out of `tracks/` and overwrote
  show audio (`tools/import_track.py`).
- **`safe_name` / `safe_subpath`** — the emulator refuses path separators and
  dot-segments in card filenames, mirroring the firmware's own checks.
- **No `shell=True`** anywhere in `tools/` or `tests/`; subprocesses get argv
  lists.

## Dependency advisories

`make audit` runs pip-audit against `requirements.lock`. Advisories in
ESPHome's build toolchain (platformio → starlette, which never sees network
input here) are ignored by id, with the list and reasoning in the `Makefile`'s
`audit` target. Everything else is expected to be clean; a new hit is a real
finding.

## If the assumption ever changes

Exposing any of this beyond the LAN means, at minimum: authentication on the
studio and the castle, `Origin`/`Host` validation, TLS, and a re-audit of every
"accepted" above. There is no partial version of that list.
