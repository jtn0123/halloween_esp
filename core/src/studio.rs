//! The studio application — tools/studio.py's shared state and page logic.
//!
//! B5 of the typesafe plan: the cue desk's local server, spoken from the
//! same crate that already owns its arithmetic. This module is the part
//! studio.py keeps for itself — where the show lives (build_paths.py's
//! sandbox rules), which page is served and how it goes lean
//! (gen_previewer.lean), the scene-id listing, and the one-release
//! /api→/studio alias table. Routes live in studio_routes.rs.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// The page rewrite lives in studio_lean.rs; the names stay here, where
/// every caller already looks for them.
pub use crate::studio_lean::{AUDIO_ROUTE, lean};

pub const API: &str = "/api/";

fn env_path(name: &str) -> Option<PathBuf> {
    std::env::var(name)
        .ok()
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

struct LeanEntry {
    key: (String, u128, u64),
    /// Shared, not copied: the lean page is ~3 MB and identical for every
    /// caller until the file under it changes, so a hit hands out a
    /// pointer and the mutex is free again immediately.
    body: Arc<Vec<u8>>,
}

pub struct App {
    pub root: PathBuf,
    pub tracks: PathBuf,
    pub scenes: PathBuf,
    /// studio.py's `_lock` — ffmpeg/render jobs and scene edits take turns.
    pub oplock: Mutex<()>,
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
            oplock: Mutex::new(()),
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
    pub fn lean_page(&self, page: &Path) -> std::io::Result<(Arc<Vec<u8>>, String)> {
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
                let body = Arc::clone(&hit.body);
                drop(slot); // before the caller does anything with 3 MB
                return Ok((body, etag));
            }
        }
        let text = std::fs::read_to_string(page)?;
        let body = Arc::new(lean(&text).into_bytes());
        *slot = Some(LeanEntry {
            key,
            body: Arc::clone(&body),
        });
        drop(slot);
        Ok((body, etag))
    }
}

/// A line the operator needs to see. The panel going empty is survivable;
/// going empty SILENTLY is what leaves someone staring at a half-edited
/// scenes.yaml wondering where the show went. Recorded as well as printed
/// under `cargo test`, so "not silent either" is an assertion and not a
/// hope.
fn warn(line: &str) {
    eprintln!("{line}");
    #[cfg(test)]
    crate::testkit::note("warn", line);
}

/// studio_scenes.scene_ids — the ids under the top-level `scenes:` key.
/// A line scan rather than a YAML parse: scenes.yaml's `  - id: ` block
/// discipline is already load-bearing (studio_scenes.block_pattern), and
/// the parity test holds this against the Python on the real file.
pub fn scene_ids(scenes: &Path) -> Vec<String> {
    let Ok(text) = std::fs::read_to_string(scenes) else {
        warn(&format!("WARNING: could not parse {}", scenes.display()));
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

/// The repo root: the ancestor holding tools/render_audio.py, found from
/// the exe's own location (core/target/release/studio) or the working dir.
///
/// The marker used to be `tools/studio.py`, which was the obvious file to
/// look for while it was the other server; it was deleted in
/// docs/RETIREMENT.md's phase 3 and the root would have silently become
/// `.`. `render_audio.py` is the better anchor anyway: it is the first
/// child a rebuild spawns, so a root without it is a root this binary
/// cannot work from.
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
            if a.join("tools").join("render_audio.py").exists() {
                return a.to_path_buf();
            }
        }
    }
    PathBuf::from(".")
}

/// What the server-ops routes asked the process to do after answering.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Action {
    None,
    Stop,
    Restart,
}

static ACTION: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

pub fn schedule(a: Action) {
    let v = match a {
        Action::None => 0,
        Action::Stop => 1,
        Action::Restart => 2,
    };
    ACTION.store(v, std::sync::atomic::Ordering::SeqCst);
}

pub fn pending() -> Action {
    match ACTION.load(std::sync::atomic::Ordering::SeqCst) {
        1 => Action::Stop,
        2 => Action::Restart,
        _ => Action::None,
    }
}

/// studio.lan_ip — best guess at this machine's address on the LAN, for
/// the `--lan` banner. A UDP socket sends nothing: connect() only asks the
/// routing table which interface would carry a packet to that address, and
/// the answer is that interface's own address. No route (no network at
/// all) is the loopback, exactly as the Python's OSError branch.
pub fn lan_ip() -> String {
    let home = "127.0.0.1".to_string();
    let Ok(s) = std::net::UdpSocket::bind("0.0.0.0:0") else {
        return home;
    };
    if s.connect("10.255.255.255:1").is_err() {
        return home;
    }
    s.local_addr().map_or(home, |a| a.ip().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jsonio::Json;
    use crate::studio_import::safe_id;
    use crate::testkit;

    fn repo() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("core/ has a parent")
            .to_path_buf()
    }

    /// Read off the real show, as the Python's twin does: the scanner has
    /// to survive whatever scenes.yaml actually looks like today, comments
    /// and all, and a duplicate id would mean the block finder is reading
    /// the wrong section.
    #[test]
    fn scene_ids_reads_the_shows_own_file() {
        let ids = scene_ids(&repo().join("scenes").join("scenes.yaml"));
        assert!(ids.contains(&"vigil".to_string()), "{ids:?}");
        let mut uniq = ids.clone();
        uniq.sort();
        uniq.dedup();
        assert_eq!(uniq.len(), ids.len(), "duplicate scene ids: {ids:?}");
    }

    /// A half-edited scenes.yaml must not take the whole panel down — and
    /// the panel must say WHY it is empty, naming the file it could not
    /// read, or the operator is left guessing at an empty scene list.
    #[test]
    fn scene_ids_survives_an_unreadable_file_and_says_why() {
        let gone = repo().join("no").join("such.yaml");
        assert_eq!(scene_ids(&gone), Vec::<String>::new());
        let said = testkit::matching("warn", "could not parse");
        assert!(
            said.iter().any(|l| l.contains("such.yaml")),
            "nothing said which file: {said:?}"
        );
    }

    /// The banner prints this to the phone/iPad operator, so it has to be
    /// an address and not a hostname or an empty string. What it IS
    /// depends on the machine's network; that it is four numbers does not.
    #[test]
    fn lan_ip_is_a_dotted_quad() {
        let ip = lan_ip();
        let parts: Vec<&str> = ip.split('.').collect();
        assert_eq!(parts.len(), 4, "{ip}");
        for p in parts {
            let n: u32 = p.parse().unwrap_or_else(|_| panic!("{ip}"));
            assert!(n <= 255, "{ip}");
        }
    }

    /// A corrupt import still has to appear in the list so it can be
    /// removed: the row carries an `error` beside the base fields instead
    /// of a duration, and nothing raises. Reached through `studio` here
    /// the way the Python's TestPureHelpers reached it, though the code
    /// itself lives in studio_tracks.rs.
    #[test]
    fn a_track_that_will_not_decode_is_reported_instead_of_raising() {
        let d = testkit::tmpdir("broken");
        let bad = d.join("_t_studio_broken.mp3");
        std::fs::write(&bad, b"not an mp3 at all").expect("wrote the fixture");
        let info = crate::studio_tracks::track_info(&bad, &Json::obj(), &d);
        assert_eq!(info.get("id"), Some(&Json::Str("_t_studio_broken".into())));
        assert!(info.get("error").is_some(), "no error on a corrupt file");
        assert!(info.get("dur").is_none(), "a duration it could not measure");
    }

    /// The write-side id guard (studio.safe_id): the browser's id lands in
    /// `--id`/`--refresh` and then in a filesystem path, so traversal, a
    /// separator, a dot, blank space and shell metacharacters all have to
    /// die HERE rather than in the importer's error output. It lives in
    /// studio_import.rs with the routes that call it; the case is the
    /// Python's TestIdGuards, which reached it through `studio`.
    #[test]
    fn an_id_the_importer_would_mint_passes_and_everything_else_dies() {
        for ok in ["chant", "organ_loop", "a1_b2_c3"] {
            assert_eq!(safe_id(ok).as_deref(), Some(ok));
        }
        for no in [
            "../../audio/01_vigil",
            "a/b",
            "a\\b",
            "a.b",
            "",
            "  ",
            "x;rm -rf",
            "a b",
        ] {
            assert_eq!(safe_id(no), None, "{no}");
        }
        // Surrounding space is trimmed rather than refused — the Python
        // strips before it checks, and a pasted id often carries one.
        assert_eq!(safe_id(" chant ").as_deref(), Some("chant"));
    }

    /// served() hands out the page and the audio directory from the SAME
    /// build. They used to be decided separately, so a sandboxed studio
    /// could serve the repo's page with the sandbox's audio and every
    /// scene link 404 — the lean rewrite names files by scene id, and the
    /// id only resolves inside the build that rendered it.
    #[test]
    fn the_page_and_its_audio_always_come_from_one_build() {
        let d =
            std::env::temp_dir().join(format!("castle-served-{:?}", std::thread::current().id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).expect("temp dir");
        let root = repo();

        // Unsandboxed: the repo's page and the repo's audio, whatever is
        // built beside the scenes file.
        let app = App::new(root.clone());
        assert!(!app.sandboxed());
        assert_eq!(
            app.served(),
            (
                root.join("previewer").join("castle-cue-desk.html"),
                root.join("audio")
            )
        );

        // Sandboxed but not built yet: the repo's pair, both halves — not
        // the repo's page with a sandbox audio directory that is empty.
        let mut sb = App::new(root.clone());
        sb.scenes = d.join("scenes.yaml");
        // build_root() without CASTLE_BUILD: beside the scenes file, so
        // this test never writes a process-wide env var other tests read.
        let build = d.join("_build");
        assert!(sb.sandboxed());
        assert_eq!(sb.build_root(), build);
        assert_eq!(
            sb.served(),
            (
                root.join("previewer").join("castle-cue-desk.html"),
                root.join("audio")
            )
        );

        // Built: the sandbox's page AND the sandbox's audio, together.
        let page = build.join("previewer").join("castle-cue-desk.html");
        std::fs::create_dir_all(page.parent().expect("parent")).expect("mkdir");
        std::fs::write(&page, "<html></html>").expect("write");
        assert_eq!(sb.served(), (page, build.join("audio")));
        let _ = std::fs::remove_dir_all(&d);
    }
}
