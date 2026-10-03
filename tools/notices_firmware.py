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
(ESP-IDF 5.5.5, xtensa-esp-elf esp-14.2.0_20260121).
"""

from __future__ import annotations

import re
from pathlib import Path

from notices_model import Component

ESPHOME = "2026.9.0"
IDF_VERSION = "5.5.5"
TOOLCHAIN = "esp-14.2.0_20260121"
#: idf_component_manager's dependencies.lock, as the build resolved it.
MANAGED = {
    "esphome/esp-audio-libs": "3.2.1",
    "esphome/micro-mp3": "0.4.0",
    "esphome/micro-opus": "0.4.1",
    "esphome/micro-wav": "0.2.0",
    "espressif/mdns": "1.12.0",
    "improv/Improv": "*",
    "zorxx/multipart-parser": "1.0.1",
}

_IDF_SRC = f"https://github.com/espressif/esp-idf/tree/v{IDF_VERSION}"
_REG = "https://components.espressif.com/components/"
_ESPHOME_WHY = (
    "The image is built on ESPHome, whose C++ runtime is published under "
    "GPLv3 only. Its terms reach the image as a whole; docs/LICENSING.md "
    '("The firmware image is a GPLv3 work") records what GPLv3 asks of '
    "whoever conveys it and the decisions still open."
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
        MANAGED["esphome/esp-audio-libs"],
        "GPL-3.0-only",
        ("Copyright (c) 2019 ESPHome",),
        source=_REG + "esphome/esp-audio-libs",
        texts=("components/esphome-LICENSE-preamble.txt",),
        note="Carries the ESPHome License; the linked files (gain.cpp, "
        "pcm_convert.cpp) are C++ and so fall under its GPLv3 half.",
        override=_ESPHOME_WHY,
    ),
    "micro-mp3": Component(
        "micro-mp3",
        MANAGED["esphome/micro-mp3"],
        "Apache-2.0",
        ("Copyright 2026 Kevin Ahrendt", "Copyright (C) 1998-2009 PacketVideo"),
        source=_REG + "esphome/micro-mp3",
        texts=("components/micro-mp3-NOTICE.txt",),
    ),
    "micro-opus": Component(
        "micro-opus (with micro-ogg-demuxer)",
        MANAGED["esphome/micro-opus"],
        "Apache-2.0",
        ("Copyright 2025 Kevin Ahrendt",),
        source=_REG + "esphome/micro-opus",
    ),
    "opus": Component(
        "Opus audio codec (as vendored by micro-opus)",
        MANAGED["esphome/micro-opus"],
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
        MANAGED["esphome/micro-wav"],
        "Apache-2.0",
        ("Copyright 2026 Kevin Ahrendt",),
        source=_REG + "esphome/micro-wav",
    ),
    "mdns": Component(
        "mDNS (espressif/mdns)",
        MANAGED["espressif/mdns"],
        "Apache-2.0",
        ("Copyright 2015-2025 Espressif Systems (Shanghai) CO LTD",),
        source=_REG + "espressif/mdns",
    ),
    "multipart-parser": Component(
        "multipart-parser",
        MANAGED["zorxx/multipart-parser"],
        "MIT",
        ("Copyright (c) 2023 Zorxx Software",),
        source=_REG + "zorxx/multipart-parser",
    ),
    "improv": Component(
        "Improv Wi-Fi SDK (C++)",
        "1.2.7",
        "Apache-2.0",
        ("No copyright line in the package; published by the Improv Wi-Fi project",),
        source="https://github.com/improv-wifi/sdk-cpp",
    ),
    "esp-idf": Component(
        "ESP-IDF",
        IDF_VERSION,
        "Apache-2.0",
        ("Copyright (C) 2015-2025 Espressif Systems (Shanghai) CO LTD",),
        source=_IDF_SRC,
        note="Espressif's framework: drivers, Wi-Fi, networking, storage, "
        "the bootloader. Third-party code inside it is listed separately.",
    ),
    "wifi-libs": Component(
        "ESP-IDF Wi-Fi and PHY libraries (precompiled)",
        IDF_VERSION,
        "Apache-2.0",
        ("Copyright (C) 2015-2025 Espressif Systems (Shanghai) CO LTD",),
        source=_IDF_SRC + "/components/esp_wifi/lib",
        note="Distributed as binaries with ESP-IDF under Apache-2.0 "
        "(components/esp_wifi/lib/LICENSE, components/esp_phy/lib/LICENSE).",
    ),
    "net80211": Component(
        "FreeBSD net80211 (in the Wi-Fi library)",
        IDF_VERSION,
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
        IDF_VERSION,
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
        IDF_VERSION,
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
        IDF_VERSION,
        "BSD-3-Clause",
        ("Copyright (C) 2006-2016 Matthew Conte",),
        source="https://github.com/espressif/tlsf",
    ),
    "sdmmc": Component(
        "SD/MMC driver (derived from OpenBSD)",
        IDF_VERSION,
        "ISC",
        ("Copyright (c) 2006 Uwe Stuehler <uwe@openbsd.org>",),
        source=_IDF_SRC + "/components/sdmmc",
    ),
    "ubsan": Component(
        "UBSAN runtime",
        IDF_VERSION,
        "BSD-2-Clause",
        (
            "Copyright (c) 2016, Linaro Limited (modified for HelenOS by Jiří Zárevúcky)",
        ),
        source=_IDF_SRC + "/components/esp_system/ubsan.c",
    ),
    "xtensa": Component(
        "Xtensa HAL and headers",
        IDF_VERSION,
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
        "these libraries under terms of your choice when it was made by an "
        "Eligible Compilation Process (GCC compiling the image, as here).",
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


_MEMBER = re.compile(r"^(\S+\.a)\(([^)]+)\)", re.MULTILINE)


def linked_archives(map_text: str) -> set[str]:
    """Archive basenames the linker pulled a member from: GNU ld's
    `Archive member included to satisfy reference by file (symbol)` block."""
    start = map_text.find("Archive member included")
    if start < 0:
        return set()
    end = map_text.find("Discarded input sections", start)
    block = map_text[start : end if end > 0 else len(map_text)]
    return {
        m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
        for m in _MEMBER.finditer(block)
    }


def _locked_versions(lock_text: str) -> dict[str, str]:
    """name -> version from dependencies.lock (a YAML file; read with a
    regex, so the release job needs no PyYAML)."""
    out: dict[str, str] = {}
    current = None
    for line in lock_text.splitlines():
        top = re.match(r"^  ([\w./-]+):\s*$", line)
        if top:
            current = top.group(1)
            continue
        ver = re.match(r"^    version: '?([^'\s]+)'?\s*$", line)
        if ver and current:
            out[current] = ver.group(1)
    return out


def check_build(build: Path) -> list[str]:
    """What a build links that the table does not cover, one line each.
    BUILD is ESPHome's build directory for the device (it holds
    dependencies.lock and build/<name>.map)."""
    errors: list[str] = []
    maps = sorted((build / "build").glob("*.map"))
    maps += sorted((build / "build" / "bootloader").glob("*.map"))
    if not maps:
        return [
            f"no linker map under {build}/build — is this an ESPHome build directory?"
        ]
    for path in maps:
        text = path.read_text(encoding="utf-8", errors="replace")
        archives = linked_archives(text)
        if not archives:
            errors.append(f"{path.name}: no archive members — not a GNU ld map?")
        errors.extend(
            f"{path.name}: {lib} is linked but not in notices_firmware.ARCHIVES"
            for lib in sorted(archives - set(ARCHIVES))
        )
        if "xtensa-esp-elf" in text and TOOLCHAIN not in text:
            errors.append(f"{path.name}: linked by a toolchain other than {TOOLCHAIN}")
    lock = build / "dependencies.lock"
    if not lock.is_file():
        return [*errors, f"{lock} is missing"]
    locked = _locked_versions(lock.read_text(encoding="utf-8"))
    if locked.get("idf") != IDF_VERSION:
        errors.append(
            f"ESP-IDF {locked.get('idf')} built this; the table is {IDF_VERSION}"
        )
    for name, version in sorted(locked.items()):
        if name != "idf" and MANAGED.get(name) != version:
            errors.append(
                f"component {name} {version} is not the reviewed one ({MANAGED.get(name)})"
            )
    errors.extend(
        f"component {name} is in the table but not in this build"
        for name in sorted(set(MANAGED) - set(locked))
    )
    version_h = build / "src" / "esphome" / "core" / "version.h"
    if version_h.is_file():
        m = re.search(
            r'ESPHOME_VERSION "([^"]+)"', version_h.read_text(encoding="utf-8")
        )
        if m and m.group(1) != ESPHOME:
            errors.append(f"ESPHome {m.group(1)} built this; the table is {ESPHOME}")
    return errors
