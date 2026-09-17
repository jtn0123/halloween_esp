"""The emulated castle's WRITE plane: PUT and DELETE, and the hand-off.

The firmware split the same way in v5.61 (firmware/sd_web_upload.h), and
for the same two reasons: the 500-line rule, and A9 — an upload is the one
request on the castle that takes minutes rather than milliseconds, and
since v5.61 it is not the control plane's work. The device hands the
request to a worker task (httpd_req_async_handler_begin) so its one httpd
task can go back to answering /api/status; this file releases the serial
lock around the same stretch, so `castle_emu --serial` — which exists to
rehearse that one task — rehearses the castle that ships.

tests/test_firmware_contract.py reads this file beside castle_emu_http.py
when it holds the emulator to the C, so a reply_err string is checked here
exactly as it was next door.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import castle_emu_wire as wire
from castle_emu_reply import NO_SD, Replies


class Uploads(Replies):
    """h_put and h_delete. Mixed into castle_emu_http.Handler, which is the
    class a server ever actually instantiates."""

    @contextmanager
    def _off_the_control_task(self) -> Iterator[None]:
        """A9 (v5.61): the part of an upload the control plane does NOT do.

        The device's httpd is one task, and --serial is how that is
        rehearsed here (castle_emu.py). Since v5.61 the bytes of an upload
        are not that task's work: h_put validates the name, hands the
        request to the upload worker (sd_web_upload.h) and returns, so
        /api/status and /api/stop keep answering for the whole of a
        publish. Releasing the serial lock around the body is the same
        sentence in Python — everything inside runs beside the control
        plane, not in front of it."""
        lock = self.server.serial
        if lock is None:  # threaded mode: there was never a queue to leave
            yield
            return
        lock.release()
        try:
            yield
        finally:
            lock.acquire()

    def h_put(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, NO_SD)
        n = self._content_len()
        if n is None:
            return self._idf(400)
        if n == 0:
            return self._err(400, "empty body")
        sub, prefix = wire.route_dir(raw)
        if sub == "site":
            # E3: a desk page has a known plausible size; the firmware
            # refuses before reading a byte.
            if n > 8 * 1024 * 1024:
                return self._err(413, "site file too large")
        name = wire.name_from_uri(raw, prefix)
        if not wire.safe_name(name):
            return self._err(400, "bad filename")
        # B3: write_body's free-space precondition (64 KB slack), when the
        # emulated card declares a size (sd_free_kb None = plenty of room).
        free_kb = self.server.sd_free_kb
        if free_kb is not None and n // 1024 + 64 > free_kb:
            return self._err(507, "not enough room on the card")
        dest = self.server.sd_dir / sub if sub else self.server.sd_dir
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / wire.fs_name(name)
        # write_body: into the sidecar, then unlink + rename (FAT's rename
        # will not overwrite). A short upload costs the sidecar only; the
        # previous copy of `target` is untouched.
        part = target.with_name(target.name + ".part")
        try:
            f = open(part, "wb")
        except OSError:
            return self._err(500, "cannot create file")
        # Everything from here is the upload worker's, not the control
        # task's (A9). The validation above stays where it was: a 400 for a
        # bad name comes back as fast as it always did.
        with self._off_the_control_task():
            self._write_upload(f, part, target, sub, n)

    def _write_upload(
        self, f: IO[bytes], part: Path, target: Path, sub: str, n: int
    ) -> None:
        """The upload worker's half of h_put: the body, the card and the
        reply. Split out so the seam the firmware now has — httpd task, then
        worker task — is the seam this file has too (A9)."""
        written = 0
        crc = 0
        with f:
            try:
                for chunk in self._body_chunks(n):
                    f.write(chunk)
                    written += len(chunk)
                    crc = zlib.crc32(chunk, crc)  # B5: sd_sync compares
            except OSError:  # TimeoutError is one of these
                pass
        if written != n:
            part.unlink(missing_ok=True)  # the sidecar only
            return self._err(500, "short write")
        # A11 (v5.61): the previous copy is MOVED aside, never deleted on
        # the promise of a rename that has not happened yet. If the rename
        # into place fails, the file that was there before is put back — a
        # failed upload costs the upload, not the show's last good track.
        keep = target.with_name(target.name + ".old")
        keep.unlink(missing_ok=True)
        had_old = False
        try:
            target.rename(keep)
            had_old = True
        except OSError:
            pass
        try:
            part.rename(target)
        except OSError:
            part.unlink(missing_ok=True)
            if had_old:
                keep.rename(target)
            return self._err(500, "rename failed")
        if had_old:
            keep.unlink(missing_ok=True)
        card = f"/sd/{sub}/{target.name}" if sub else f"/sd/{target.name}"
        self._json({"path": card, "bytes": written, "crc32": "%08x" % crc})

    def h_delete(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, NO_SD)
        sub, prefix = wire.route_dir(raw)
        name = wire.name_from_uri(raw, prefix)
        if not wire.safe_name(name):
            return self._err(400, "bad filename")
        dest = self.server.sd_dir / sub if sub else self.server.sd_dir
        try:
            (dest / wire.fs_name(name)).unlink()
        except OSError:
            return self._err(404, "no such file")
        self._json({"deleted": True})
