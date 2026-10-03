"""tools/desktop_release.py + desktop_thirdparty.py: which release, which bytes.

No socket is opened: every download is a `Fetch` backed by a dict of
canned bodies, so the checksum, zip and API logic is exercised for real
against fixtures and the network is never the thing under test.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_release as rel
import desktop_thirdparty as tp


def zip_bytes(members: dict[str, bytes], mode: int = 0o755) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zf.writestr(info, body)
    return buf.getvalue()


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def fake_fetch(pages: dict[str, bytes]) -> rel.Fetch:
    def fetch(url: str) -> bytes:
        if url not in pages:
            raise OSError(f"no route to {url} in this test")
        return pages[url]

    return fetch


def api_body(tag: str, assets: dict[str, str], **extra: object) -> bytes:
    return json.dumps(
        {
            "tag_name": tag,
            "assets": [
                {"name": n, "browser_download_url": u} for n, u in assets.items()
            ],
            **extra,
        }
    ).encode()


class TestTargets(unittest.TestCase):
    def test_rust_targets_match_the_release_contract(self) -> None:
        self.assertEqual(rel.rust_target("Darwin", "arm64"), "aarch64-apple-darwin")
        self.assertEqual(rel.rust_target("Darwin", "x86_64"), "x86_64-apple-darwin")
        self.assertEqual(rel.rust_target("Windows", "AMD64"), "x86_64-pc-windows-msvc")
        self.assertIsNone(rel.rust_target("Windows", "ARM64"))
        self.assertIsNone(rel.rust_target("Linux", "x86_64"))
        self.assertEqual(
            rel.core_asset("x86_64-pc-windows-msvc", "v0.1.0"),
            "castle-core-x86_64-pc-windows-msvc-v0.1.0.zip",
        )


class TestReleaseApi(unittest.TestCase):
    def test_latest_is_one_call_and_parses_assets(self) -> None:
        calls: list[str] = []
        body = api_body("v0.2.0", {"SHA256SUMS": "https://x/sums"})

        def fetch(url: str) -> bytes:
            calls.append(url)
            return body

        r = rel.find_release(fetch)
        self.assertEqual(calls, [rel.API_LATEST])
        self.assertEqual(r.tag, "v0.2.0")
        self.assertEqual(r.assets["SHA256SUMS"], "https://x/sums")
        self.assertTrue(r.source_zip.endswith("/archive/refs/tags/v0.2.0.zip"))

    def test_named_tag_uses_the_tag_route(self) -> None:
        url = rel.API_TAG.format(tag="v0.1.0")
        r = rel.find_release(fake_fetch({url: api_body("v0.1.0", {})}), "v0.1.0")
        self.assertEqual(r.tag, "v0.1.0")

    def test_prereleases_drafts_and_garbage_are_refused(self) -> None:
        for body in (
            api_body("v0.3.0", {}, prerelease=True),
            api_body("v0.3.0", {}, draft=True),
            b"<html>rate limited</html>",
            b"{}",
        ):
            with self.subTest(body=body[:30]), self.assertRaises(rel.ReleaseError):
                rel.release_from_api(body)

    def test_offline_is_a_release_error_not_a_traceback(self) -> None:
        offline = fake_fetch({})
        with self.assertRaises(rel.ReleaseError):
            rel.find_release(offline)


class TestChecksums(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_parse_sums_accepts_sha256sum_format(self) -> None:
        a, b = "a" * 64, "B" * 64
        sums = rel.parse_sums(f"{a}  one.zip\n\n# note\n{b} *two.bin\n")
        self.assertEqual(sums, {"one.zip": a, "two.bin": b.lower()})
        with self.assertRaises(rel.ReleaseError):
            rel.parse_sums("not-a-hash  file\n")

    def test_verified_asset_round_trip_and_tamper(self) -> None:
        good = zip_bytes({"studio": b"bin"})
        sums = f"{sha(good)}  core.zip\n".encode()
        r = rel.Release("v0.1.0", {"core.zip": "u/core", "SHA256SUMS": "u/sums"})
        path = rel.fetch_verified_asset(
            r, "core.zip", self.tmp, fake_fetch({"u/core": good, "u/sums": sums})
        )
        self.assertEqual(path.read_bytes(), good)
        bad = fake_fetch({"u/core": good + b"x", "u/sums": sums})
        with self.assertRaises(rel.ReleaseError):
            rel.fetch_verified_asset(r, "core.zip", self.tmp / "t", bad)
        self.assertFalse(
            (self.tmp / "t" / "core.zip").exists(), "a bad download is deleted"
        )

    def test_no_sums_no_install(self) -> None:
        nothing = fake_fetch({})
        for assets in ({"core.zip": "u/core"}, {"SHA256SUMS": "u/sums"}):
            r = rel.Release("v0.1.0", assets)
            with self.subTest(assets=assets), self.assertRaises(rel.ReleaseError):
                rel.fetch_verified_asset(r, "core.zip", self.tmp, nothing)

    def test_safe_extract_refuses_zip_slip(self) -> None:
        archive = self.tmp / "evil.zip"
        archive.write_bytes(zip_bytes({"../escape.txt": b"x"}))
        with self.assertRaises(rel.ReleaseError):
            rel.safe_extract(archive, self.tmp / "out")
        self.assertFalse((self.tmp / "escape.txt").exists())

    def test_core_bins_placed_from_any_depth(self) -> None:
        archive = self.tmp / "core.zip"
        archive.write_bytes(
            zip_bytes({f"castle-core/{n}.exe": n.encode() for n in rel.CORE_BINS})
        )
        files = rel.safe_extract(archive, self.tmp / "x")
        placed = rel.place_core_bins(files, self.tmp / "release", ".exe")
        self.assertEqual(
            sorted(p.name for p in placed), sorted(f"{n}.exe" for n in rel.CORE_BINS)
        )
        with self.assertRaises(rel.ReleaseError):
            rel.place_core_bins(files, self.tmp / "release", "")

    def test_single_root_unwraps_github_source_zips(self) -> None:
        (self.tmp / "halloween_esp-0.1.0" / "tools").mkdir(parents=True)
        self.assertEqual(rel.single_root(self.tmp).name, "halloween_esp-0.1.0")
        (self.tmp / "second").mkdir()
        self.assertEqual(rel.single_root(self.tmp), self.tmp)


class TestThirdParty(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_every_pin_is_a_full_sha256(self) -> None:
        for key, pins in tp.FFMPEG_PINS.items():
            for pin in pins:
                with self.subTest(platform=key, url=pin.url):
                    self.assertRegex(pin.sha256, r"^[0-9a-f]{64}$")
                    self.assertTrue(pin.url.startswith("https://"))

    def test_package_managers(self) -> None:
        has = {"winget": "w", "brew": "b"}.get
        winget = tp.package_manager_command("Windows", has)
        assert winget is not None
        self.assertEqual(winget[:2], ["winget", "install"])
        self.assertEqual(
            tp.package_manager_command("Darwin", has), ["brew", "install", "ffmpeg"]
        )
        self.assertIsNone(tp.package_manager_command("Darwin", lambda _n: None))
        self.assertIsNone(tp.package_manager_command("Linux", has))

    def test_ffmpeg_on_path_needs_both_halves(self) -> None:
        exe = str
        self.assertIsNone(tp.ffmpeg_on_path({"ffmpeg": "/f"}.get, exe))
        both = {"ffmpeg": "/f", "ffprobe": "/p"}.get
        self.assertEqual(tp.ffmpeg_on_path(both, exe), ("/f", "/p"))

    def test_pinned_ffmpeg_is_verified_and_unpacked(self) -> None:
        body = zip_bytes(
            {"ffmpeg-9/bin/ffmpeg.exe": b"ff", "ffmpeg-9/bin/ffprobe.exe": b"fp"}
        )
        pin = tp.Pin("https://pin/ff.zip", sha(body), ("ffmpeg.exe", "ffprobe.exe"))
        with mock.patch.dict(tp.FFMPEG_PINS, {("Windows", "x86_64"): (pin,)}):
            ff, probe = tp.fetch_pinned_ffmpeg(
                "Windows",
                "AMD64",
                self.tmp / "bin",
                fake_fetch({pin.url: body}),
                self.tmp,
            )
            self.assertEqual(Path(ff).read_bytes(), b"ff")
            self.assertEqual(Path(probe).name, "ffprobe.exe")
            tampered, b2 = fake_fetch({pin.url: body + b"!"}), self.tmp / "b2"
            with self.assertRaises(rel.ReleaseError):
                tp.fetch_pinned_ffmpeg("Windows", "AMD64", b2, tampered, self.tmp)
        nothing = fake_fetch({})
        with self.assertRaises(rel.ReleaseError):
            tp.fetch_pinned_ffmpeg("Linux", "riscv64", self.tmp, nothing, self.tmp)

    def test_ytdlp_is_checked_against_its_release_sums(self) -> None:
        body = b"#!yt-dlp"
        pages = {
            tp.YTDLP_BASE + tp.YTDLP_SUMS: f"{sha(body)}  yt-dlp_macos\n".encode(),
            tp.YTDLP_BASE + "yt-dlp_macos": body,
        }
        out = tp.fetch_ytdlp("Darwin", self.tmp, fake_fetch(pages))
        self.assertEqual(Path(out).name, "yt-dlp")
        self.assertEqual(Path(out).read_bytes(), body)
        pages[tp.YTDLP_BASE + "yt-dlp_macos"] = b"tampered"
        tampered, dest = fake_fetch(pages), self.tmp / "t"
        with self.assertRaises(rel.ReleaseError):
            tp.fetch_ytdlp("Darwin", dest, tampered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
