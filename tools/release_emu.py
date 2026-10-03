"""A fake GitHub Releases API, so an update can be rehearsed with no GitHub.

castle_emu.py stands in for the castle; this stands in for the other end of
an update — the three Releases-API answers the apps read (`latest`, the
list, one tag) and the asset downloads they lead to, from one local port.
Nothing here is reached by default: the code under test is handed
`ReleaseEmu.fetch`, which sends what was addressed to api.github.com here
instead, and every asset URL it hands out already points here.

`firmware_release` builds what a release's firmware half IS, by the names
and the SHA256SUMS format tools/release_assets.py stages — so a test can
publish a release and then break exactly one thing about it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import desktop_release as rel
import release_assets as ra
import release_channel as channel

API = "https://api.github.com"
#: The real GET, held here so a test that swaps desktop_release.http_fetch
#: for `ReleaseEmu.fetch` does not send this one round in a circle.
_GET = rel.http_fetch
REPO_PATH = f"/repos/{rel.REPO}/releases"


def sums(files: dict[str, bytes]) -> bytes:
    """SHA256SUMS over `files`, in sha256sum's text format."""
    return "".join(
        f"{hashlib.sha256(blob).hexdigest()}  {name}\n"
        for name, blob in sorted(files.items())
    ).encode()


def firmware_release(
    tag: str,
    version: str,
    image: bytes,
    board: str = ra.BOARD,
    fw_variant: str = ra.FW_VARIANT,
) -> dict[str, bytes]:
    """A release's firmware assets for one board, checksummed."""
    about = {**ra.firmware_about(tag, len(image), version), "board": board}
    about["fw_variant"] = fw_variant
    prefix = f"castle-fw-{board}-{tag}"
    files = {
        f"{prefix}.ota.bin": image,
        f"{prefix}.factory.bin": b"\xe9factory" + image,
        f"{prefix}.json": (json.dumps(about, indent=2) + "\n").encode(),
    }
    return {**files, rel.SUMS: sums(files)}


class Handler(BaseHTTPRequestHandler):
    server: ReleaseEmu

    def log_message(self, *_args: Any) -> None:
        return

    def send(self, code: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        emu = self.server
        path = self.path.split("?", 1)[0]
        emu.hits.append(self.path)
        if path in emu.blobs:
            return self.send(200, emu.blobs[path], "application/octet-stream")
        rows = emu.rows()
        if path == REPO_PATH:
            return self.send(200, json.dumps(rows).encode())
        if path == f"{REPO_PATH}/latest":
            stable = [r for r in rows if not r["prerelease"] and not r["draft"]]
            if stable:
                return self.send(200, json.dumps(stable[0]).encode())
        if path.startswith(f"{REPO_PATH}/tags/"):
            tag = path.rsplit("/", 1)[1]
            for row in rows:
                if row["tag_name"] == tag:
                    return self.send(200, json.dumps(row).encode())
        self.send(404, b'{"message": "Not Found"}')


class ReleaseEmu(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self._releases: list[dict[str, Any]] = []
        self.blobs: dict[str, bytes] = {}
        self.hits: list[str] = []

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def publish(
        self,
        tag: str,
        files: dict[str, bytes],
        prerelease: bool | None = None,
        draft: bool = False,
    ) -> None:
        """A release with these assets; a `-suffix` tag is a pre-release, as
        the release workflow marks it, unless `prerelease` says otherwise."""
        if prerelease is None:
            prerelease = not channel.accepts(tag, False)
        assets = []
        for name, blob in files.items():
            path = f"/download/{tag}/{name}"
            self.blobs[path] = blob
            assets.append({"name": name, "browser_download_url": self.base + path})
        self._releases.append(
            {
                "tag_name": tag,
                "prerelease": prerelease,
                "draft": draft,
                "assets": assets,
            }
        )

    def rows(self) -> list[dict[str, Any]]:
        """The list endpoint's answer: newest first, as GitHub orders it."""
        return sorted(
            self._releases,
            key=lambda r: channel.version_key(r["tag_name"]) or ((0, 0, 0), 0, ()),
            reverse=True,
        )

    def fetch(self, url: str) -> bytes:
        """The Fetch to hand the code under test: GitHub's API, answered here."""
        return _GET(url.replace(API, self.base, 1))

    def start(self) -> ReleaseEmu:
        threading.Thread(
            target=self.serve_forever, args=(0.05,), daemon=True, name="release-emu"
        ).start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
