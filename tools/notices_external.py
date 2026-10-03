"""What the castle's software USES but does not hand over: fetched by the
buyer's own machine from its publisher, at install time or first use.

None of it is inside an artifact the release publishes — the source zip,
the castle-core zips, the desktop app, the firmware image, the web flasher
— and tests/test_third_party_notices.py keeps that true: the desktop app's
bundled resources and the release job's sidecar step are pinned there, so
putting ffmpeg, Python or a model INTO a bundle fails until its notices
(and, for ffmpeg, an LGPL build) come with it. The table is printed in the
notices anyway, so a buyer can see what the installer will download and
under whose terms.

Every row is reviewed by hand; the source of each claim is in its text.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class External:
    name: str
    version: str
    terms: str
    obtained: str
    note: str = ""


EXTERNAL: tuple[External, ...] = (
    External(
        "FFmpeg (ffmpeg, ffprobe)",
        "9.0.2 static builds (tools/desktop_thirdparty.py FFMPEG_PINS)",
        "GPL-3.0 (these builds enable GPL components); FFmpeg itself is "
        "LGPL-2.1-or-later when built without them",
        "the installer uses one already on PATH, else winget (Gyan.FFmpeg) or "
        "Homebrew, else downloads the pinned build from its publisher "
        "(github.com/GyanD/codexffmpeg, ffmpeg.martin-riedl.de) and checks "
        "its published sha256",
        "Run as a separate program, never linked. Bundling it would make it "
        "a redistributed component: only an LGPL build (no --enable-gpl, no "
        "--enable-nonfree), with its licence text and source offer.",
    ),
    External(
        "yt-dlp",
        "latest stable release, replaced only by Update the downloader",
        "Unlicense (public domain dedication); its standalone binary bundles "
        "third-party code listed in its own THIRD_PARTY_LICENSES.txt",
        "downloaded from github.com/yt-dlp/yt-dlp releases — by the installer, "
        "and by Castle Radio's Update the downloader button — into per-user "
        "app data, and checked against that same release's SHA2-256SUMS "
        "before it is ever run",
        "Kept out of the app bundle on purpose (docs/PRODUCTION-TODO.md §3): "
        "websites change under it every few weeks, so it is updated on its "
        "own schedule, by the owner, and never in the middle of an import.",
    ),
    External(
        "Demucs",
        "4.1.0 (requirements-desktop.lock)",
        'MIT ("Demucs is released under the MIT license")',
        "installed from PyPI by uv into the app's own Python environment",
    ),
    External(
        "htdemucs model weights",
        "signature 955717e8",
        "No licence statement found: the Hugging Face model card "
        "(huggingface.co/adefossez/HTDemucs) declares none, and the demucs "
        "package's MIT statement names its code. The demucs README describes "
        "the model as trained on MUSDB HQ plus an extra dataset of 800 songs.",
        "downloaded by demucs on first use: the Hugging Face hub "
        "(adefossez/HTDemucs), falling back to dl.fbaipublicfiles.com/demucs/",
        "An open decision before the weights are ever bundled — docs/LICENSING.md.",
    ),
    External(
        "PyTorch, NumPy, SciPy and the rest of requirements-desktop.lock",
        "the pins in requirements-desktop.lock",
        "each package's own licence (PyTorch, NumPy and SciPy are BSD-3-Clause "
        "and bundle further libraries under their own terms)",
        "installed from PyPI by uv, hash-checked against the lock",
    ),
    External(
        "Python 3.13",
        "uv's managed CPython (python-build-standalone)",
        "PSF-2.0, plus the licences of the libraries it is built with "
        "(OpenSSL, SQLite, libffi, zlib, xz, bzip2, mpdecimal, Tcl/Tk)",
        "installed by uv (`uv python install 3.13`) from its publisher",
    ),
    External(
        "uv",
        "the installer script's current release",
        "MIT OR Apache-2.0",
        "installed from astral.sh by the installer",
    ),
    External(
        "Microsoft Edge WebView2 Runtime (Windows)",
        "whatever Windows has, else Microsoft's current one",
        "Microsoft's WebView2 Runtime licence terms",
        "already part of Windows 10/11; when missing, the app's installer "
        "downloads Microsoft's bootstrapper (Tauri's downloadBootstrapper mode)",
    ),
    External(
        "esp-web-tools",
        "10.4.0 (flasher/index.html, pinned with an integrity hash)",
        "Apache-2.0",
        "loaded by the visitor's browser from unpkg.com when the web flasher "
        "page opens; the page itself does not carry it",
    ),
    External(
        "DM Sans and Manrope (fonts)",
        "Google Fonts' current files",
        "SIL Open Font License 1.1",
        "loaded by the browser from fonts.googleapis.com when a Castle Radio "
        "page opens",
    ),
)
