"""What the buyer's firmware image links, and under which terms — a
reviewed table, checked against a real build.

The image (`castle-fw-<board>-<tag>.factory.bin` / `.ota.bin`, built from
firmware/castle_buyer.yaml) is ESPHome + ESP-IDF + the components below,
compiled together with the castle's own configuration and headers. None of
that can be read from a lockfile, so the table here is written by hand from
one build's linker maps (`Archive member included` — what the linker pulled
in, not what was compiled) and the sources behind them, and
`third_party_notices.py check-firmware BUILD_DIR` re-reads a build's maps
in CI: an archive this table does not know, or a pinned version that moved,
fails the release before the image is staged.

Reviewed 2026-10-02 against an esphome 2026.9.0 compile of castle_buyer.yaml
(ESP-IDF 5.5.5, xtensa-esp-elf esp-14.2.0_20260121). Re-checked 2026-10-06
against a 2026.9.1 compile: the same ESP-IDF, toolchain, managed components
and linked archives, and ESPHome's LICENSE unchanged between the two tags.
"""

from __future__ import annotations

import re
from pathlib import Path

from notices_model import Component, shown

ESPHOME = "2026.9.1"
IDF_VERSION = "5.5.5"
TOOLCHAIN = "esp-14.2.0_20260121"
#: The managed components, by their component-registry names.
AUDIO_LIBS = "esphome/esp-audio-libs"
MICRO_MP3 = "esphome/micro-mp3"
MICRO_OPUS = "esphome/micro-opus"
MICRO_WAV = "esphome/micro-wav"
MDNS = "espressif/mdns"
IMPROV = "improv/Improv"
MULTIPART = "zorxx/multipart-parser"
#: idf_component_manager's dependencies.lock, as the build resolved it.
MANAGED = {
    AUDIO_LIBS: "3.2.1",
    MICRO_MP3: "0.4.0",
    MICRO_OPUS: "0.4.1",
    MICRO_WAV: "0.2.0",
    MDNS: "1.12.0",
    IMPROV: "*",
    MULTIPART: "1.0.1",
}
#: The licence most of the image's components come under.
APACHE = "Apache-2.0"

_IDF_SRC = f"https://github.com/espressif/esp-idf/tree/v{IDF_VERSION}"
_REG = "https://components.espressif.com/components/"
_IN_IDF = f"as included in ESP-IDF {IDF_VERSION}"
_ESPHOME_WHY = (
    "The image is built on ESPHome, whose C++ runtime is published under "
    "GPLv3 only, and that runtime is compiled into it. docs/LICENSING.md "
    '("The firmware image and GPLv3") quotes what GPLv3 asks of whoever '
    "conveys such an image, and lists the decisions about it still open."
)

FIRMWARE: dict[str, Component] = {
    "esphome": Component(
        "ESPHome (C++ runtime and components)",
        ESPHOME,
        "GPL-3.0-only",
        ("Copyright (c) 2019 ESPHome",),
        source=f"https://github.com/esphome/esphome/tree/{ESPHOME}",
        texts=("components/esphome-LICENSE-preamble.txt",),
        note="The ESPHome License puts the C++ runtime under GPLv3 and the "
        "Python code generator under MIT; only the runtime is in the image.",
        override=_ESPHOME_WHY,
    ),
    "esp-audio-libs": Component(
        "esp-audio-libs",
        MANAGED[AUDIO_LIBS],
        "GPL-3.0-only",
        ("Copyright (c) 2019 ESPHome",),
        source=_REG + AUDIO_LIBS,
        texts=("components/esphome-LICENSE-preamble.txt",),
        note="Carries the ESPHome License; the linked files (gain.cpp, "
        "pcm_convert.cpp) are C++ and so fall under its GPLv3 half.",
        override=_ESPHOME_WHY,
    ),
    "micro-mp3": Component(
        "micro-mp3",
        MANAGED[MICRO_MP3],
        APACHE,
        ("Copyright 2026 Kevin Ahrendt", "Copyright (C) 1998-2009 PacketVideo"),
        source=_REG + MICRO_MP3,
        texts=("components/micro-mp3-NOTICE.txt",),
    ),
    "micro-opus": Component(
        "micro-opus (with micro-ogg-demuxer)",
        MANAGED[MICRO_OPUS],
        APACHE,
        ("Copyright 2025 Kevin Ahrendt",),
        source=_REG + MICRO_OPUS,
    ),
    "opus": Component(
        "Opus audio codec",
        f"as vendored in micro-opus {MANAGED[MICRO_OPUS]}",
        "BSD-3-Clause",
        (
            (
                "Copyright 2001-2023 Xiph.Org, Skype Limited, Octasic, Jean-Marc "
                "Valin, Timothy B. Terriberry, CSIRO, Gregory Maxwell, Mark "
                "Borgerding, Erik de Castro Lopo, Mozilla, Amazon"
            ),
            "Copyright (c) 2013, Koen Vos",
            "Copyright (c) 2024 Arm Limited",
        ),
        source="https://opus-codec.org/",
        texts=("components/opus-COPYING.txt",),
    ),
    "micro-wav": Component(
        "micro-wav",
        MANAGED[MICRO_WAV],
        APACHE,
        ("Copyright 2026 Kevin Ahrendt",),
        source=_REG + MICRO_WAV,
    ),
    "mdns": Component(
        "mDNS (espressif/mdns)",
        MANAGED[MDNS],
        APACHE,
        ("Copyright 2015-2025 Espressif Systems (Shanghai) CO LTD",),
        source=_REG + MDNS,
    ),
    "multipart-parser": Component(
        "multipart-parser",
        MANAGED[MULTIPART],
        "MIT",
        ("Copyright (c) 2023 Zorxx Software",),
        source=_REG + MULTIPART,
    ),
    "improv": Component(
        "Improv Wi-Fi SDK (C++)",
        "1.2.7",
        APACHE,
        ("No copyright line in the package; published by the Improv Wi-Fi project",),
        source="https://github.com/improv-wifi/sdk-cpp",
    ),
    "esp-idf": Component(
        "ESP-IDF",
        IDF_VERSION,
        APACHE,
        ("Copyright (C) 2015-2025 Espressif Systems (Shanghai) CO LTD",),
        source=_IDF_SRC,
        note="Espressif's framework: drivers, Wi-Fi, networking, storage, "
        "the bootloader. Third-party code inside it is listed separately.",
    ),
    "wifi-libs": Component(
        "ESP-IDF Wi-Fi and PHY libraries (precompiled)",
        IDF_VERSION,
        APACHE,
        ("Copyright (C) 2015-2025 Espressif Systems (Shanghai) CO LTD",),
        source=_IDF_SRC + "/components/esp_wifi/lib",
        note="Distributed as binaries with ESP-IDF under Apache-2.0 "
        "(components/esp_wifi/lib/LICENSE, components/esp_phy/lib/LICENSE).",
    ),
    "net80211": Component(
        "FreeBSD net80211 (in the Wi-Fi library)",
        _IN_IDF,
        # ESP-IDF's COPYRIGHT.rst says "BSD License"; FreeBSD's
        # sys/net80211 sources carry the two-clause text:
        # https://github.com/freebsd/freebsd-src/blob/main/sys/net80211/ieee80211.c
        "BSD-2-Clause",
        ("Copyright (C) 2004-2008 Sam Leffler, Errno Consulting",),
        source="https://github.com/freebsd/freebsd-src/tree/main/sys/net80211",
    ),
    "freertos": Component(
        "FreeRTOS kernel (ESP-IDF port)",
        "V10.5.1",
        "MIT",
        (
            "Copyright (C) 2021 Amazon.com, Inc. or its affiliates",
            "Copyright (C) 2015-2019 Cadence Design Systems, Inc.",
        ),
        source=_IDF_SRC + "/components/freertos",
        texts=("components/freertos-LICENSE.txt",),
    ),
    "lwip": Component(
        "lwIP",
        "2.2.0 (ESP-IDF fork)",
        "BSD-3-Clause",
        (
            "Copyright (c) 2001-2004 Swedish Institute of Computer Science",
            "Copyright (c) 2001-2004 Leon Woestenberg",
            "Copyright (c) 2001-2004 Axon Digital Design B.V., The Netherlands",
            "Copyright (c) 2002 CITEL Technologies Ltd.",
            "Copyright (c) 2002-2003, Adam Dunkels (uIP)",
            "Copyright (c) 2007-2009 Frédéric Bernon, Simon Goldschmidt",
            "Copyright (c) 2016 The MINIX 3 Project",
        ),
        source=_IDF_SRC + "/components/lwip",
        texts=("components/lwip-COPYING.txt",),
    ),
    "mbedtls": Component(
        "Mbed TLS",
        "3.6.6",
        "Apache-2.0 OR GPL-2.0-or-later",
        ("Copyright The Mbed TLS Contributors",),
        source=_IDF_SRC + "/components/mbedtls",
    ),
    "ca-bundle": Component(
        "Mozilla CA certificate list (ESP-IDF certificate bundle)",
        "2025-02-25",
        "MPL-2.0",
        (
            (
                "Certificate data from Mozilla's NSS certdata.txt as of "
                "2025-02-25, converted by curl's mk-ca-bundle.pl 1.29"
            ),
        ),
        source="https://hg.mozilla.org/releases/mozilla-release/raw-file/"
        "default/security/nss/lib/ckfw/builtins/certdata.txt",
        note="ESP-IDF embeds the common subset of this list for verifying "
        "TLS servers; Mozilla publishes the source file under MPL-2.0.",
    ),
    "wpa_supplicant": Component(
        "wpa_supplicant",
        _IN_IDF,
        "BSD-3-Clause",
        (
            "Copyright (c) 2002-2022, Jouni Malinen <j@w1.fi> and contributors",
            "Copyright (c) 2019-2020, The Linux Foundation",
            "Copyright (c) 2013 Cozybit, Inc.",
        ),
        source=_IDF_SRC + "/components/wpa_supplicant",
    ),
    "http_parser": Component(
        "HTTP Parser (NGINX / Node.js derived)",
        _IN_IDF,
        "MIT",
        ("Copyright Igor Sysoev", "Copyright Joyent, Inc. and other Node contributors"),
        source=_IDF_SRC + "/components/http_parser",
        texts=("components/http_parser-LICENSE.txt",),
    ),
    "fatfs": Component(
        "FatFs",
        "R0.15",
        "LicenseRef-FatFs",
        ("Copyright (C) 2022, ChaN, all right reserved.",),
        source=_IDF_SRC + "/components/fatfs",
        texts=("components/fatfs-ff.h-header.txt",),
    ),
    "tlsf": Component(
        "TLSF allocator",
        _IN_IDF,
        "BSD-3-Clause",
        ("Copyright (C) 2006-2016 Matthew Conte",),
        source="https://github.com/espressif/tlsf",
    ),
    "sdmmc": Component(
        "SD/MMC driver (derived from OpenBSD)",
        _IN_IDF,
        "ISC",
        ("Copyright (c) 2006 Uwe Stuehler <uwe@openbsd.org>",),
        source=_IDF_SRC + "/components/sdmmc",
    ),
    "ubsan": Component(
        "UBSAN runtime",
        _IN_IDF,
        "BSD-2-Clause",
        (
            "Copyright (c) 2016, Linaro Limited (modified for HelenOS by Jiří Zárevúcky)",
        ),
        source=_IDF_SRC + "/components/esp_system/ubsan.c",
    ),
    "xtensa": Component(
        "Xtensa HAL and headers",
        _IN_IDF,
        "MIT",
        (
            "Copyright (C) 2003, 2006, 2010 Tensilica Inc.",
            "Copyright (c) 2015-2019 Cadence Design Systems, Inc.",
        ),
        source=_IDF_SRC + "/components/xtensa",
    ),
    "newlib": Component(
        "newlib C library (libc, libm)",
        "4.3.0",
        "LicenseRef-newlib",
        ("Copyright the respective parties named in COPYING.NEWLIB, reproduced below",),
        source="https://github.com/espressif/newlib-esp32",
        texts=("components/newlib-COPYING.NEWLIB.txt",),
    ),
    "gcc-runtime": Component(
        "GCC runtime libraries (libgcc, libstdc++)",
        "14.2.0",
        "GPL-3.0-or-later WITH GCC-exception-3.1",
        (
            "Copyright (C) 1987-2024 Free Software Foundation, Inc.",
            "Copyright (c) 1994 Hewlett-Packard Company",
            "Copyright (c) 1996 Silicon Graphics Computer Systems, Inc.",
        ),
        source="https://github.com/espressif/gcc",
        texts=("components/libstdcxx-hp-sgi.txt",),
        override="Linked under the GCC Runtime Library Exception 3.1, whose "
        "section 1 permits propagating a work of Target Code formed with "
        "these libraries under terms of your choice when it is made by an "
        "Eligible Compilation Process as that exception defines it. The "
        "image is compiled by this GCC toolchain, from source.",
    ),
}

#: Linker archive -> the components whose code it holds. Every archive in a
#: map must be here; ESP-IDF's own libraries are listed once, below.
ARCHIVES: dict[str, tuple[str, ...]] = {
    "libsrc.a": ("esphome",),
    "libesphome__esp-audio-libs.a": ("esp-audio-libs",),
    "libesphome__micro-mp3.a": ("micro-mp3",),
    "libesphome__micro-opus.a": ("micro-opus", "opus"),
    "libmicro_ogg_demuxer.a": ("micro-opus",),
    "libesphome__micro-wav.a": ("micro-wav",),
    "libespressif__mdns.a": ("mdns",),
    "libzorxx__multipart-parser.a": ("multipart-parser",),
    "libImprov.a": ("improv",),
    "libnet80211.a": ("wifi-libs", "net80211"),
    "libfreertos.a": ("esp-idf", "freertos"),
    "liblwip.a": ("esp-idf", "lwip"),
    "libesp_netif.a": ("esp-idf", "lwip"),
    "libmbedcrypto.a": ("esp-idf", "mbedtls"),
    "libmbedtls.a": ("esp-idf", "mbedtls", "ca-bundle"),
    "libmbedx509.a": ("mbedtls",),
    "libwpa_supplicant.a": ("esp-idf", "wpa_supplicant"),
    "libhttp_parser.a": ("http_parser",),
    "libfatfs.a": ("esp-idf", "fatfs"),
    "libheap.a": ("esp-idf", "tlsf"),
    "libsdmmc.a": ("esp-idf", "sdmmc"),
    "libesp_system.a": ("esp-idf", "ubsan"),
    "libxtensa.a": ("esp-idf", "xtensa"),
    "libxt_hal.a": ("xtensa",),
    "libc.a": ("newlib",),
    "libm.a": ("newlib",),
    "libgcc.a": ("gcc-runtime",),
    "libstdc++.a": ("gcc-runtime",),
}
for _lib in (
    "libpp.a",
    "libphy.a",
    "libbtbb.a",
    "libcore.a",
    "libespnow.a",
    "libmesh.a",
):
    ARCHIVES[_lib] = ("wifi-libs",)
for _name in (
    "app_update bootloader_support cxx efuse esp-tls esp_app_format "
    "esp_bootloader_format esp_coex esp_common esp_driver_gpio esp_driver_i2s "
    "esp_driver_rmt esp_driver_sdmmc esp_driver_sdspi esp_driver_spi "
    "esp_driver_uart esp_driver_usb_serial_jtag esp_event esp_http_client "
    "esp_http_server esp_hw_support esp_mm esp_partition esp_phy esp_pm "
    "esp_psram esp_ringbuf esp_rom esp_security esp_timer esp_vfs_console "
    "esp_wifi hal log main newlib nvs_flash pthread soc spi_flash "
    "tcp_transport vfs"
).split():
    ARCHIVES[f"lib{_name}.a"] = ("esp-idf",)


def components() -> list[Component]:
    """The image's third-party components, in the table's order."""
    return list(FIRMWARE.values())


def _archive(line: str) -> str | None:
    """The archive basename of one `archive(member)` line of the block, or
    None. Read by hand, not by regex: a pattern such as `\\S+\\.a\\(` backtracks
    over every dot in a long path, and a map has thousands of these lines.
    The reference that may follow on the same line is past the first space."""
    if not line or line[0].isspace():
        return None
    head, sep, member = line.split(maxsplit=1)[0].rpartition(".a(")
    if not (head and sep and len(member) > 1 and member.endswith(")")):
        return None
    return (head + ".a").replace("\\", "/").rsplit("/", 1)[-1]


def linked_archives(map_text: str) -> set[str]:
    """Archive basenames the linker pulled a member from: GNU ld's
    `Archive member included to satisfy reference by file (symbol)` block."""
    start = map_text.find("Archive member included")
    if start < 0:
        return set()
    end = map_text.find("Discarded input sections", start)
    block = map_text[start : end if end > 0 else len(map_text)]
    return {a for a in map(_archive, block.splitlines()) if a}


_LOCK_NAME = re.compile(r"^ {2}([\w./-]+):\s*$")
_LOCK_VERSION = re.compile(r"^ {4}version: '?([^'\s]+)'?\s*$")


def _locked_versions(lock_text: str) -> dict[str, str]:
    """name -> version from dependencies.lock (a YAML file; read with a
    regex, so the release job needs no PyYAML)."""
    out: dict[str, str] = {}
    current = None
    for line in lock_text.splitlines():
        top = _LOCK_NAME.match(line)
        if top:
            current = top.group(1)
            continue
        ver = _LOCK_VERSION.match(line)
        if ver and current:
            out[current] = ver.group(1)
    return out


def _map_errors(path: Path) -> list[str]:
    """One linker map: every archive it pulled from is in ARCHIVES, and the
    toolchain that linked it is the reviewed one."""
    text = path.read_text(encoding="utf-8", errors="replace")
    archives = linked_archives(text)
    errors = (
        [] if archives else [f"{path.name}: no archive members — not a GNU ld map?"]
    )
    errors.extend(
        f"{path.name}: {lib} is linked but not in notices_firmware.ARCHIVES"
        for lib in sorted(archives - set(ARCHIVES))
    )
    if "xtensa-esp-elf" in text and TOOLCHAIN not in text:
        errors.append(f"{path.name}: linked by a toolchain other than {TOOLCHAIN}")
    return errors


def _lock_errors(locked: dict[str, str]) -> list[str]:
    """dependencies.lock: the reviewed ESP-IDF and managed components, all
    of them and no others."""
    errors = []
    if locked.get("idf") != IDF_VERSION:
        errors.append(
            f"ESP-IDF {locked.get('idf')} built this; the table is {IDF_VERSION}"
        )
    errors.extend(
        f"component {name} {version} is not the reviewed one ({MANAGED.get(name)})"
        for name, version in sorted(locked.items())
        if name != "idf" and MANAGED.get(name) != version
    )
    errors.extend(
        f"component {name} is in the table but not in this build"
        for name in sorted(set(MANAGED) - set(locked))
    )
    return errors


def _esphome_errors(version_h: Path) -> list[str]:
    """ESPHome's own version.h, when the build tree has one."""
    if not version_h.is_file():
        return []
    m = re.search(r'ESPHOME_VERSION "([^"]+)"', version_h.read_text(encoding="utf-8"))
    if m and m.group(1) != ESPHOME:
        return [f"ESPHome {m.group(1)} built this; the table is {ESPHOME}"]
    return []


def check_build(build: Path) -> list[str]:
    """What a build links that the table does not cover, one line each.
    BUILD is ESPHome's build directory for the device (it holds
    dependencies.lock and build/<name>.map)."""
    maps = sorted((build / "build").glob("*.map"))
    maps += sorted((build / "build" / "bootloader").glob("*.map"))
    if not maps:
        where = shown(build / "build")
        return [f"no linker map under {where} — is this an ESPHome build directory?"]
    errors = [e for path in maps for e in _map_errors(path)]
    lock = build / "dependencies.lock"
    if not lock.is_file():
        return [*errors, f"{shown(lock)} is missing"]
    errors += _lock_errors(_locked_versions(lock.read_text(encoding="utf-8")))
    return errors + _esphome_errors(build / "src" / "esphome" / "core" / "version.h")
