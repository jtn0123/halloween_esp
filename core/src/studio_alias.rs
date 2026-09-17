//! The deprecated `/api/…` spellings of the studio's own routes — one
//! table, one rewrite, one DEPRECATED line per route.
//!
//! Split from [`crate::studio`] at the repo's 500-line cap: that module is
//! the application's state, this is a compatibility shim with an end date
//! (v5.24 plus one release, docs/API.md). When the aliases go, so does this
//! file, and nothing else has to be picked apart to remove them.

use std::collections::HashSet;
use std::sync::{Mutex, OnceLock};

use crate::studio::API;

/// The studio's own route families — /api/<x> for any of these is the old
/// spelling, rewritten for one release. studio_http.STUDIO_ROUTES.
pub const STUDIO_ROUTES: [&str; 14] = [
    "tracks", "import", "job", "refresh", "track", "waveform", "stems", "stem", "compare", "probe",
    "server", "scene", "rebuild", "card",
];

fn deprecated_seen() -> &'static Mutex<HashSet<String>> {
    static SEEN: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    SEEN.get_or_init(|| Mutex::new(HashSet::new()))
}

/// studio_http.studio_path — the request's path (no query), an old /api/
/// spelling of a studio route rewritten to its /studio/ home, logged once.
pub fn studio_path(target: &str) -> String {
    let path = target.split('?').next().unwrap_or("");
    let Some(rest) = path.strip_prefix(API) else {
        return path.to_string();
    };
    let head = rest.split('/').next().unwrap_or("");
    let fire = head == "scene"
        && crate::httpd::query_pairs(target)
            .iter()
            .any(|(k, _)| k == "s");
    if !STUDIO_ROUTES.contains(&head) || fire {
        return path.to_string();
    }
    let mut seen = deprecated_seen().lock().unwrap_or_else(|e| e.into_inner());
    if seen.insert(head.to_string()) {
        eprintln!(
            "  DEPRECATED: /api/{head} is now /studio/{head} (docs/API.md) — \
             the alias goes away next release"
        );
    }
    format!("/studio/{rest}")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The old spellings, aliased for one release: /api/<studio route> is
    /// the desk's own business and lands on /studio/, and the query goes —
    /// the caller matches on a path, never on a path plus its arguments.
    #[test]
    fn the_old_api_spellings_of_studio_routes_are_rewritten() {
        for (old, new) in [
            ("/api/tracks", "/studio/tracks"),
            ("/api/tracks/x?scene=1", "/studio/tracks/x"),
            ("/api/import/async", "/studio/import/async"),
            ("/api/scene", "/studio/scene"),
            ("/api/card/a.mp3", "/studio/card/a.mp3"),
            ("/api/server/stop", "/studio/server/stop"),
        ] {
            assert_eq!(studio_path(old), new, "{old}");
        }
    }

    /// Everything else is somebody else's: /api/status and friends are the
    /// castle's and relay untouched, /studio and the page are already home.
    /// `/api/scene?s=…` is the trap — same head as the studio's scene
    /// editor, but with `s` it is the castle's "play this scene", so it
    /// must NOT be rewritten out from under the relay.
    #[test]
    fn castle_and_studio_routes_pass_through_with_the_query_stripped() {
        for same in [
            "/api/status",
            "/api/files/x.mp3",
            "/api/scene?s=vigil",
            "/api/play?f=x.mp3",
            "/studio/tracks",
            "/remote",
            "/",
        ] {
            let want = same.split('?').next().unwrap_or("");
            assert_eq!(studio_path(same), want, "{same}");
        }
    }

    /// docs/API.md and the handler agree on what the studio owns. The
    /// Python's STUDIO_ROUTES is a set of these same fourteen names; this
    /// side keeps them in the order the docs list them, so the assertion
    /// is on the whole table rather than on membership.
    #[test]
    fn every_studio_route_family_is_in_the_table() {
        assert_eq!(
            STUDIO_ROUTES,
            [
                "tracks", "import", "job", "refresh", "track", "waveform", "stems", "stem",
                "compare", "probe", "server", "scene", "rebuild", "card",
            ]
        );
    }
}
