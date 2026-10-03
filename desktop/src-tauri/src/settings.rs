//! `settings.json` in the per-user app config dir — everything the owner
//! may need to point the app at, none of it required. A missing file is the
//! defaults; a broken one is the defaults plus a line in the log, never a
//! refusal to start.
//!
//! ```json
//! { "castle_host": "192.168.1.50",
//!   "castle_key": "the key the castle was locked with",
//!   "install_dir": "C:\\Users\\me\\CastleTools",
//!   "python": "C:\\Users\\me\\CastleTools\\.venv\\Scripts\\python.exe" }
//! ```

use serde::Deserialize;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

/// The castle key (firmware v5.74), held so that no `{:?}` of the settings
/// can print it into the log.
#[derive(Clone, Default, Deserialize)]
#[serde(transparent)]
pub struct CastleKey(String);

impl CastleKey {
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for CastleKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("CastleKey(..)")
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    /// The castle Castle Radio's device bridge talks to (`CASTLE_RADIO_HOST`).
    /// Unset leaves the bridge's own default.
    pub castle_host: Option<String>,
    /// The castle's key, for an owner who would rather type it here than in
    /// the app's Settings: it reaches both children as `CASTLE_KEY`, which
    /// wins over the key store (docs/notes/06-buyer-build.md). Unset passes
    /// nothing, so the store (and a developer's own CASTLE_KEY) still work.
    pub castle_key: Option<CastleKey>,
    /// A configured install (the option-A uv installer's tree, or any
    /// checkout) — second in line after a bundled sidecar.
    pub install_dir: Option<PathBuf>,
    /// The interpreter for that install, when it is not one of the venvs
    /// the app looks for itself.
    pub python: Option<PathBuf>,
}

pub const FILE_NAME: &str = "settings.json";

/// The settings, and a sentence for the log when the file was unusable.
pub fn load(config_dir: &Path) -> (Settings, Option<String>) {
    let path = config_dir.join(FILE_NAME);
    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (Settings::default(), None),
        Err(e) => {
            return (
                Settings::default(),
                Some(format!("{}: {e}", path.display())),
            )
        }
    };
    match serde_json::from_str::<Settings>(&text) {
        Ok(mut settings) => {
            let mut warning = None;
            if let Some(host) = &settings.castle_host {
                if !valid_host(host) {
                    warning = Some(format!("{}: castle_host {host:?} ignored", path.display()));
                    settings.castle_host = None;
                }
            }
            if let Some(key) = settings.castle_key.take() {
                let key = key.expose().trim();
                if valid_key(key) {
                    settings.castle_key = Some(CastleKey(key.to_owned()));
                } else if !key.is_empty() {
                    // The rule, never the value: this line goes to the log.
                    warning = Some(format!(
                        "{}: castle_key ignored (1-64 printable characters, no spaces)",
                        path.display()
                    ));
                }
            }
            (settings, warning)
        }
        Err(e) => (
            Settings::default(),
            Some(format!("{}: {e}", path.display())),
        ),
    }
}

/// A host name, IPv4 address or `host:port` — the bridge builds
/// `http://{host}{path}` from it, so nothing that could change the URL's
/// shape (a slash, an `@`, a space) gets through.
pub fn valid_host(host: &str) -> bool {
    !host.is_empty()
        && host.len() <= 253
        && host
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | ':'))
}

/// What firmware v5.74 can hold (`key_chars_ok` in sd_web_prefs.h): 1-64
/// bytes, `!` to `~`. tools/hosts.py `valid_key` and castle-core's are the
/// same rule; a key outside it would never be sent, so it is never passed.
pub fn valid_key(key: &str) -> bool {
    (1..=64).contains(&key.len()) && key.bytes().all(|b| (b'!'..=b'~').contains(&b))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hosts() {
        assert!(valid_host("castle-feather-s3.local"));
        assert!(valid_host("192.168.1.50:8080"));
        assert!(!valid_host(""));
        assert!(!valid_host("evil.com/x"));
        assert!(!valid_host("user@castle"));
    }

    #[test]
    fn missing_broken_and_good_files() {
        let dir = std::env::temp_dir().join(format!("castle-settings-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let (s, w) = load(&dir);
        assert!(s.castle_host.is_none() && w.is_none());
        fs::write(dir.join(FILE_NAME), "{not json").unwrap();
        assert!(load(&dir).1.is_some());
        fs::write(dir.join(FILE_NAME), r#"{"castle_host":"10.0.0.9"}"#).unwrap();
        assert_eq!(load(&dir).0.castle_host.as_deref(), Some("10.0.0.9"));
        fs::write(dir.join(FILE_NAME), r#"{"castle_host":"a/b"}"#).unwrap();
        let (s, w) = load(&dir);
        assert!(s.castle_host.is_none() && w.is_some());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn a_castle_key_is_passed_trimmed_or_dropped_unsaid() {
        let dir = std::env::temp_dir().join(format!("castle-key-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let key = |json: &str| {
            fs::write(dir.join(FILE_NAME), json).unwrap();
            let (s, w) = load(&dir);
            (s.castle_key.map(|k| k.expose().to_owned()), w)
        };
        let (k, w) = key(r#"{"castle_key":" pa\"ss#1\t"}"#);
        assert_eq!((k.as_deref(), w), (Some("pa\"ss#1"), None));
        assert_eq!(key(r#"{"castle_key":""}"#), (None, None));
        let long = "k".repeat(65);
        for bad in ["two words", "caf\u{e9}", long.as_str()] {
            let (k, w) = key(&format!(r#"{{"castle_key":"{bad}"}}"#));
            let w = w.expect("an unusable key is said, not passed");
            assert!(k.is_none() && w.contains("castle_key ignored"));
            assert!(
                !w.contains(bad),
                "the warning names the rule, never the key"
            );
        }
        fs::write(dir.join(FILE_NAME), r#"{"castle_key":"s3cret"}"#).unwrap();
        let shown = format!("{:?}", load(&dir).0);
        assert!(shown.contains("CastleKey(..)") && !shown.contains("s3cret"));
        assert!(valid_key("!~") && !valid_key("") && !valid_key("a b"));
        let _ = fs::remove_dir_all(dir);
    }
}
