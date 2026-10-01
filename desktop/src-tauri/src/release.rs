//! The release contract, as the app reads it.
//!
//! `tools/release_assets.py` spells every GitHub Release asset name once;
//! this is the app's copy of the names it is written against — the updater's
//! tag rule now, and the castle firmware check (PRODUCTION-TODO §9: compare
//! `/api/status` `board` + firmware with the Release, fetch the OTA image)
//! when that lands. Change one, change the other: the parity pairs are
//! listed in desktop/README.md "Release contract".

use serde::Serialize;

/// What the firmware reports as `board` in /api/status (ESP32-S3 Feather
/// #5477: 4 MB flash, 2 MB PSRAM); an image matches a castle by equality.
pub const BOARD: &str = "feather-s3-4m2p";

/// The rust targets a Release ships castle-core for (`CORE_TARGETS`).
pub const CORE_TARGETS: [&str; 3] = [
    "x86_64-pc-windows-msvc",
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
];

/// The target this app was compiled for (build.rs passes cargo's TARGET).
pub const TARGET: &str = env!("CASTLE_TARGET");

/// A release tag, split: `vMAJOR.MINOR.PATCH[-suffix]` (release_assets.py
/// `TAG_RE`). A suffix makes it a pre-release.
#[derive(Debug, PartialEq, Eq)]
pub struct Tag<'a> {
    pub version: (u64, u64, u64),
    pub suffix: Option<&'a str>,
}

fn number(s: &str) -> Option<u64> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    s.parse().ok()
}

pub fn parse_tag(tag: &str) -> Option<Tag<'_>> {
    let rest = tag.strip_prefix('v')?;
    let (core, suffix) = match rest.split_once('-') {
        Some((core, suffix)) => (core, Some(suffix)),
        None => (rest, None),
    };
    if let Some(sfx) = suffix {
        let mut bytes = sfx.bytes();
        let first_ok = bytes.next().is_some_and(|b| b.is_ascii_alphanumeric());
        if !first_ok || !bytes.all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-') {
            return None;
        }
    }
    let mut parts = core.split('.');
    let version = (
        number(parts.next()?)?,
        number(parts.next()?)?,
        number(parts.next()?)?,
    );
    if parts.next().is_some() {
        return None;
    }
    Some(Tag { version, suffix })
}

/// The tag a version string is released under (`0.2.0` → `v0.2.0`).
pub fn tag(version: &str) -> String {
    format!("v{version}")
}

/// True only for a well-formed stable tag — what the buyer's channel takes.
pub fn is_stable(tag: &str) -> bool {
    parse_tag(tag).is_some_and(|t| t.suffix.is_none())
}

pub fn factory_name(tag: &str) -> String {
    format!("castle-fw-{BOARD}-{tag}.factory.bin")
}

pub fn ota_name(tag: &str) -> String {
    format!("castle-fw-{BOARD}-{tag}.ota.bin")
}

pub fn core_zip_name(target: &str, tag: &str) -> String {
    format!("castle-core-{target}-{tag}.zip")
}

/// The assets of the Release this build belongs to, by name.
#[derive(Debug, Serialize)]
pub struct Assets {
    pub tag: String,
    pub board: &'static str,
    pub firmware_ota: String,
    pub firmware_factory: String,
    /// None on a target the Release does not ship castle-core for.
    pub castle_core: Option<String>,
}

pub fn assets(version: &str) -> Assets {
    let tag = tag(version);
    Assets {
        board: BOARD,
        firmware_ota: ota_name(&tag),
        firmware_factory: factory_name(&tag),
        castle_core: CORE_TARGETS
            .contains(&TARGET)
            .then(|| core_zip_name(TARGET, &tag)),
        tag,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tags_follow_release_assets_tag_re() {
        assert_eq!(
            parse_tag("v1.2.3"),
            Some(Tag {
                version: (1, 2, 3),
                suffix: None
            })
        );
        assert_eq!(parse_tag("v0.10.0-rc.1").unwrap().suffix, Some("rc.1"));
        for bad in [
            "1.2.3",
            "v1.2",
            "v1.2.3.4",
            "v1..3",
            "v1.2.3-",
            "v1.2.3-.x",
            "v1.2.3-a_b",
            "va.b.c",
            "",
        ] {
            assert_eq!(parse_tag(bad), None, "{bad}");
        }
        assert!(is_stable("v1.2.3"));
        assert!(!is_stable("v1.2.3-beta"));
        assert!(!is_stable("1.2.3"));
    }

    #[test]
    fn names_match_the_contract() {
        // The literal strings tools/release_assets.py produces for v1.2.3.
        assert_eq!(
            ota_name("v1.2.3"),
            "castle-fw-feather-s3-4m2p-v1.2.3.ota.bin"
        );
        assert_eq!(
            factory_name("v1.2.3"),
            "castle-fw-feather-s3-4m2p-v1.2.3.factory.bin"
        );
        assert_eq!(
            core_zip_name("aarch64-apple-darwin", "v1.2.3"),
            "castle-core-aarch64-apple-darwin-v1.2.3.zip"
        );
    }

    #[test]
    fn this_builds_assets() {
        let a = assets("0.1.0");
        assert_eq!(a.tag, "v0.1.0");
        assert_eq!(a.firmware_ota, "castle-fw-feather-s3-4m2p-v0.1.0.ota.bin");
        assert_eq!(a.castle_core.is_some(), CORE_TARGETS.contains(&TARGET));
    }
}
