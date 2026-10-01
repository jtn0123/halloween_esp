//! `castle-tools://start` — the one action a web page may ask of this app.
//!
//! The same rule as the Swift launcher it replaces: a fixed action only. A
//! URL never supplies a command, a path, a host or a port; anything other
//! than the start action is logged and dropped.

pub const START: &str = "castle-tools://start";

/// True only for the start action. Browsers on Windows hand the URL over
/// with a trailing slash added (`castle-tools://start/`), so that one
/// spelling is accepted too — nothing else: no query, no fragment, no path.
pub fn is_start(url: &str) -> bool {
    url == START || url.strip_suffix('/') == Some(START)
}

/// Whether a process's argv carries a castle-tools URL at all (how the
/// single-instance plugin hands a second launch to the first on Windows).
pub fn in_args<S: AsRef<str>>(args: &[S]) -> bool {
    args.iter()
        .any(|a| a.as_ref().to_ascii_lowercase().starts_with("castle-tools:"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_fixed_action() {
        assert!(is_start("castle-tools://start"));
        assert!(is_start("castle-tools://start/"));
        for refused in [
            "castle-tools://start//",
            "castle-tools://start?cmd=rm",
            "castle-tools://start#x",
            "castle-tools://start/../../etc",
            "castle-tools://stop",
            "castle-tools://START",
            "castle-tools:start",
            "https://castle-tools/start",
            "",
        ] {
            assert!(!is_start(refused), "{refused}");
        }
    }

    #[test]
    fn spots_urls_in_argv() {
        assert!(in_args(&["app.exe", "castle-tools://start/"]));
        assert!(!in_args(&["app.exe", "--flag"]));
    }
}
