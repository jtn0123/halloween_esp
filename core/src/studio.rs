//! The studio application — tools/studio.py's shared state and page logic.
//!
//! B5 of the typesafe plan: the cue desk's local server, spoken from the
//! same crate that already owns its arithmetic. This module is the part
//! studio.py keeps for itself — where the show lives (build_paths.py's
//! sandbox rules), which page is served and how it goes lean
//! (gen_previewer.lean), the scene-id listing, and the one-release
//! /api→/studio alias table. Routes live in studio_routes.rs.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

pub const AUDIO_ROUTE: &str = "/studio/scene-audio/";
pub const API: &str = "/api/";

fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var(name)
        .ok()
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

struct LeanEntry {
    key: (String, u128, u64),
    body: Vec<u8>,
}

pub struct App {
    pub root: PathBuf,
    pub tracks: PathBuf,
    pub scenes: PathBuf,
    lean: Mutex<Option<LeanEntry>>,
}

impl App {
    pub fn new(root: PathBuf) -> App {
        let tracks = env_path("CASTLE_TRACKS").unwrap_or_else(|| root.join("tracks"));
        let scenes =
            env_path("CASTLE_SCENES").unwrap_or_else(|| root.join("scenes").join("scenes.yaml"));
        App {
            root,
            tracks,
            scenes,
            lean: Mutex::new(None),
        }
    }

    fn canon(p: &Path) -> PathBuf {
        p.canonicalize().unwrap_or_else(|_| p.to_path_buf())
    }

    /// build_paths.sandboxed(): the scenes file is not the repo's own.
    pub fn sandboxed(&self) -> bool {
        Self::canon(&self.scenes) != Self::canon(&self.root.join("scenes").join("scenes.yaml"))
    }

    /// build_paths.build_root().
    pub fn build_root(&self) -> PathBuf {
        if let Some(b) = env_path("CASTLE_BUILD") {
            return b;
        }
        if self.sandboxed() {
            self.scenes
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| self.root.clone())
                .join("_build")
        } else {
            self.root.clone()
        }
    }

    /// studio.served(): the page the studio serves and the audio/ it was
    /// built from — a sandbox's own build once it has one, the repo's until
    /// then; always both, so the lean links resolve to its own files.
    pub fn served(&self) -> (PathBuf, PathBuf) {
        let build = self.build_root();
        let page = build.join("previewer").join("castle-cue-desk.html");
        if self.sandboxed() && page.exists() {
            (page, build.join("audio"))
        } else {
            (
                self.root.join("previewer").join("castle-cue-desk.html"),
                self.root.join("audio"),
            )
        }
    }

    /// gen_previewer.lean_page: (body, etag), computed once per
    /// (path, mtime, size) — the rewrite is one pass over ~2.4 MB.
    pub fn lean_page(&self, page: &Path) -> std::io::Result<(Vec<u8>, String)> {
        let md = std::fs::metadata(page)?;
        let mtime = md
            .modified()?
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let size = md.len();
        let key = (page.to_string_lossy().into_owned(), mtime, size);
        let etag = format!("\"{mtime}-{size}-lean\"");
        let mut slot = self.lean.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(hit) = slot.as_ref() {
            if hit.key == key {
                return Ok((hit.body.clone(), etag));
            }
        }
        let text = std::fs::read_to_string(page)?;
        let body = lean(&text).into_bytes();
        *slot = Some(LeanEntry {
            key,
            body: body.clone(),
        });
        Ok((body, etag))
    }
}

/// gen_previewer.lean — every inlined scene audio swapped for its URL:
/// `"(\w+)": ?"data:audio/mpeg;base64,…"` becomes `"<id>": "<route><id>"`.
pub fn lean(html: &str) -> String {
    const NEEDLE: &str = "data:audio/mpeg;base64,";
    let b = html.as_bytes();
    let mut out = String::with_capacity(html.len() / 4);
    let mut copied = 0usize;
    let mut from = 0usize;
    while let Some(rel) = html[from..].find(NEEDLE) {
        let pos = from + rel;
        from = pos + NEEDLE.len();
        if pos == 0 || b[pos - 1] != b'"' {
            continue;
        }
        // Walk back over `": ?"` to the key, which must be "\w+".
        let mut k = pos - 1;
        if k > 0 && b[k - 1] == b' ' {
            k -= 1;
        }
        if k == 0 || b[k - 1] != b':' {
            continue;
        }
        k -= 1;
        if k == 0 || b[k - 1] != b'"' {
            continue;
        }
        k -= 1;
        let mut id_start = k;
        while id_start > 0 && (b[id_start - 1].is_ascii_alphanumeric() || b[id_start - 1] == b'_') {
            id_start -= 1;
        }
        if id_start == k || id_start == 0 || b[id_start - 1] != b'"' {
            continue;
        }
        let id = &html[id_start..k];
        // Forward over the base64 body to the closing quote.
        let mut end = pos + NEEDLE.len();
        while end < b.len()
            && (b[end].is_ascii_alphanumeric() || matches!(b[end], b'+' | b'/' | b'='))
        {
            end += 1;
        }
        if end >= b.len() || b[end] != b'"' {
            continue;
        }
        out.push_str(&html[copied..id_start - 1]);
        out.push('"');
        out.push_str(id);
        out.push_str("\": \"");
        out.push_str(AUDIO_ROUTE);
        out.push_str(id);
        out.push('"');
        copied = end + 1;
        from = end + 1;
    }
    out.push_str(&html[copied..]);
    out
}

/// studio_scenes.scene_ids — the ids under the top-level `scenes:` key.
/// A line scan rather than a YAML parse: scenes.yaml's `  - id: ` block
/// discipline is already load-bearing (studio_scenes.block_pattern), and
/// the parity test holds this against the Python on the real file.
pub fn scene_ids(scenes: &Path) -> Vec<String> {
    let Ok(text) = std::fs::read_to_string(scenes) else {
        eprintln!("WARNING: could not parse {}", scenes.display());
        return Vec::new();
    };
    let mut section = String::new();
    let mut out = Vec::new();
    for line in text.lines() {
        match line.chars().next() {
            Some(c) if c != ' ' && c != '\t' && c != '#' => {
                if line.contains(':') {
                    section = line.split(':').next().unwrap_or("").trim().to_string();
                }
                continue;
            }
            _ => {}
        }
        if section == "scenes" {
            if let Some(rest) = line.strip_prefix("  - id: ") {
                let v = rest
                    .split('#')
                    .next()
                    .unwrap_or("")
                    .trim()
                    .trim_matches(|c| c == '"' || c == '\'');
                if !v.is_empty() {
                    out.push(v.to_string());
                }
            }
        }
    }
    out
}

/// gen_previewer.scene_audio: the rendered NN_<sid>.mp3 for a scene, the id
/// matched as a whole name — no separators, no traversal.
pub fn scene_audio(audio_dir: &Path, sid: &str) -> Option<PathBuf> {
    if sid.is_empty() || !sid.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        return None;
    }
    let mut hits: Vec<PathBuf> = std::fs::read_dir(audio_dir)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name().and_then(|n| n.to_str()).is_some_and(|n| {
                let nb = n.as_bytes();
                n.len() == sid.len() + 7
                    && nb[0].is_ascii_digit()
                    && nb[1].is_ascii_digit()
                    && nb[2] == b'_'
                    && &n[3..3 + sid.len()] == sid
                    && n.ends_with(".mp3")
            })
        })
        .collect();
    hits.sort();
    hits.into_iter().next()
}

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

/// The repo root: the ancestor holding tools/studio.py, found from the
/// exe's own location (core/target/release/studio) or the working dir.
pub fn repo_root() -> PathBuf {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd.join("_"));
    }
    for s in &starts {
        for a in s.ancestors() {
            if a.join("tools").join("studio.py").exists() {
                return a.to_path_buf();
            }
        }
    }
    PathBuf::from(".")
}
