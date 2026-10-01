//! `settings.json` in the per-user app config dir — everything the owner
//! may need to point the app at, none of it required. A missing file is the
//! defaults; a broken one is the defaults plus a line in the log, never a
//! refusal to start.
//!
//! ```json
//! { "castle_host": "192.168.1.50",
//!   "install_dir": "C:\\Users\\me\\CastleTools",
//!   "python": "C:\\Users\\me\\CastleTools\\.venv\\Scripts\\python.exe" }
//! ```

use serde::Deserialize;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    /// The castle Castle Radio's device bridge talks to (`CASTLE_RADIO_HOST`).
    /// Unset leaves the bridge's own default.
    pub castle_host: Option<String>,
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
}
