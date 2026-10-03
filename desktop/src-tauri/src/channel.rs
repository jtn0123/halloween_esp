//! Which app updates the owner is offered: tools/release_channel.py's rule,
//! held here for the one check the app makes itself.
//!
//! Stable only, unless the owner opted in — `"prerelease": true` in
//! settings.json, or CASTLE_PRERELEASE in the environment, which wins when
//! it is set, exactly as it does for every Python tool. There is no switch
//! on any page, on purpose. The app hands the decision to both servers as
//! CASTLE_PRERELEASE=1 (runtime::child_env), so Castle Radio's "Update
//! castle" and this updater answer alike.
//!
//! Opted in, WHICH release is newest is Castle Radio's to say
//! (`GET /radio/app/release`): tools/release_channel.py decides it once, with
//! the one GitHub list call, and this side only checks what it is handed.
//! Only the answer's tag is taken, and only a release tag builds a URL, so
//! no answer can point the updater anywhere but this repo's own release
//! downloads — and the update itself is still minisign-verified. No answer
//! (Radio down, GitHub unreachable) checks the stable manifest instead.
//!
//! tests/test_release_channel.py reads CASES, OPT_IN and the two URLs below
//! and holds the Python module to them.

use crate::release;
use std::time::Duration;

pub const ENV: &str = "CASTLE_PRERELEASE";
/// tauri.conf.json's endpoint: GitHub's `latest` never names a pre-release.
pub const STABLE: &str =
    "https://github.com/jtn0123/halloween_esp/releases/latest/download/latest.json";
/// One release's manifest, by tag (release_channel.py `LATEST_JSON`).
const LATEST_JSON: &str =
    "https://github.com/jtn0123/halloween_esp/releases/download/{tag}/latest.json";
/// Castle Radio's answer to "which release does this owner's channel offer".
pub const ROUTE: &str = "/radio/app/release";
/// That answer may wait on GitHub, so it gets longer than an identity probe.
pub const WAIT: Duration = Duration::from_secs(30);

/// CASTLE_PRERELEASE when it is set to anything, else the setting.
pub fn opted_in(setting: bool, env: Option<&str>) -> bool {
    match env.map(str::trim).filter(|v| !v.is_empty()) {
        Some(v) => matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on"),
        None => setting,
    }
}

/// The decision for this process: the setting, and this process's env.
pub fn opted_in_now(setting: bool) -> bool {
    opted_in(setting, std::env::var(ENV).ok().as_deref())
}

/// Whether this channel may offer `tag` at all.
pub fn accepts(tag: &str, prerelease: bool) -> bool {
    release::parse_tag(tag).is_some_and(|t| prerelease || t.suffix.is_none())
}

/// The latest.json a release carries, for a release tag and nothing else.
pub fn manifest(tag: &str) -> Option<String> {
    release::parse_tag(tag).map(|_| LATEST_JSON.replace("{tag}", tag))
}

/// Where this check reads latest.json, and — when an opted-in check had to
/// settle for the stable manifest — a line for the log saying why.
/// `answer` is Castle Radio's `/radio/app/release` reply, if it gave one.
pub fn endpoint(prerelease: bool, answer: Option<&serde_json::Value>) -> (String, Option<String>) {
    if !prerelease {
        return (STABLE.to_owned(), None);
    }
    let tag = answer.and_then(|v| v["tag"].as_str()).unwrap_or("");
    match manifest(tag) {
        Some(url) => (url, None),
        None => (
            STABLE.to_owned(),
            Some(format!(
                "pre-release channel: Castle Radio named no release ({tag:?}); checking the stable one"
            )),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// (tag, the stable channel takes it, the pre-release channel takes it)
    const CASES: &[(&str, bool, bool)] = &[
        ("v1.2.3", true, true),
        ("v0.10.0", true, true),
        ("v1.2.3-rc.1", false, true),
        ("v1.2.3-beta", false, true),
        ("v1.2.3-rc-2.x", false, true),
        ("1.2.3", false, false),
        ("v1.2", false, false),
        ("v1.2.3.4", false, false),
        ("v1..3", false, false),
        ("v1.2.3-", false, false),
        ("v1.2.3-.x", false, false),
        ("v1.2.3-a_b", false, false),
        ("va.b.c", false, false),
        ("nightly", false, false),
        ("", false, false),
    ];

    /// (the setting, CASTLE_PRERELEASE, opted in)
    const OPT_IN: &[(bool, Option<&str>, bool)] = &[
        (false, None, false),
        (true, None, true),
        (false, Some("1"), true),
        (false, Some(" TRUE "), true),
        (false, Some("on"), true),
        (true, Some("0"), false),
        (true, Some("no"), false),
        (true, Some(""), true),
        (false, Some("  "), false),
    ];

    #[test]
    fn the_channels_take_the_tags_the_python_rule_takes() {
        for &(tag, stable, early) in CASES {
            assert_eq!(accepts(tag, false), stable, "stable {tag:?}");
            assert_eq!(accepts(tag, true), early, "pre-release {tag:?}");
            assert_eq!(manifest(tag).is_some(), early, "manifest {tag:?}");
        }
    }

    #[test]
    fn the_environment_wins_when_it_is_set() {
        for &(setting, env, want) in OPT_IN {
            assert_eq!(opted_in(setting, env), want, "{setting} {env:?}");
        }
    }

    #[test]
    fn only_a_release_tag_moves_the_endpoint() {
        let rc = json!({"tag": "v1.2.0-rc.1", "latest_json": "https://evil.example/x"});
        assert_eq!(endpoint(false, Some(&rc)), (STABLE.to_owned(), None));
        assert_eq!(
            endpoint(true, Some(&rc)).0,
            "https://github.com/jtn0123/halloween_esp/releases/download/v1.2.0-rc.1/latest.json"
        );
        for answer in [
            None,
            Some(json!({"error": "cannot reach GitHub"})),
            Some(json!({"tag": "../../evil"})),
            Some(json!({"tag": 7})),
        ] {
            let (url, note) = endpoint(true, answer.as_ref());
            assert_eq!(url, STABLE, "{answer:?}");
            assert!(note.is_some_and(|n| n.contains("stable")), "{answer:?}");
        }
    }

    #[test]
    fn castle_radio_is_asked_over_the_loopback() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut conn, _) = listener.accept().unwrap();
            let mut buf = [0u8; 512];
            let n = conn.read(&mut buf).unwrap();
            assert!(String::from_utf8_lossy(&buf[..n]).starts_with("GET /radio/app/release "));
            conn.write_all(b"HTTP/1.0 200 OK\r\n\r\n{\"tag\":\"v2.0.0-beta.3\"}")
                .unwrap();
        });
        let answer = crate::probe::get_json(port, ROUTE, WAIT);
        server.join().unwrap();
        assert!(endpoint(true, answer.as_ref())
            .0
            .ends_with("/v2.0.0-beta.3/latest.json"));
        // Nobody answering is None. Port 0, not the port just freed: another
        // test may be handed that one and answer (probe.rs, the same flake).
        assert_eq!(crate::probe::get_json(0, ROUTE, WAIT), None);
    }

    #[test]
    fn both_servers_are_told_the_decision() {
        use crate::runtime::{child_env, DataDirs, Runtime, Source};
        use std::path::{Path, PathBuf};
        let rt = Runtime {
            source: Source::Checkout,
            root: PathBuf::from("/r"),
            python: PathBuf::from("/r/.venv/bin/python"),
            bin_dir: None,
            models: None,
            studio: None,
        };
        let data = DataDirs::new(Path::new("/d"));
        for setting in [false, true] {
            let settings = crate::settings::Settings {
                prerelease: setting,
                ..Default::default()
            };
            let told = child_env(&rt, &data, &settings, None)
                .iter()
                .any(|(k, v)| k == ENV && v == "1");
            assert_eq!(told, opted_in_now(setting), "setting {setting}");
        }
    }
}
