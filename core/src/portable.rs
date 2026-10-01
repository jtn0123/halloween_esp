//! The two questions whose answer depends on the platform the studio runs
//! on, answered once: which external program to start, and what part of a
//! name the network handed us is a file name.
//!
//! **Programs.** ffmpeg and yt-dlp are asked for in three places, first
//! answer wins: `CASTLE_FFMPEG` / `CASTLE_YTDLP` when set and non-empty
//! (the launcher naming them, the same way `CASTLE_PY` names its
//! interpreter; the Python children inherit the same variables), then a
//! sidecar — `ffmpeg` / `yt-dlp` in the directory the running binary sits
//! in, which is where a packaged app ships its copies — then PATH. On
//! Windows the sidecar and the PATH search both add `.exe`, as the shell
//! would.
//!
//! **Names.** A route or an upload that carries a name keeps only its last
//! segment, which is what stops `../../x` from reaching outside the
//! library. On Windows `\` separates as well as `/`, and a drive prefix
//! (`C:x`) turns a join into a different root — so both separators are cut
//! on every platform (no track id or upload the studio keeps has a `\` in
//! it, and one rule is a rule the macOS tests check), and `:` is cut on
//! Windows, where it can never be part of a file name.

use std::ffi::OsString;
use std::path::Path;

/// The ffmpeg to run: `CASTLE_FFMPEG`, a sidecar, else `ffmpeg` from PATH.
pub fn ffmpeg() -> OsString {
    program("CASTLE_FFMPEG", "ffmpeg")
}

/// The yt-dlp to run: `CASTLE_YTDLP`, a sidecar, else `yt-dlp` from PATH.
pub fn yt_dlp() -> OsString {
    program("CASTLE_YTDLP", "yt-dlp")
}

/// Is yt-dlp there to be run? The named file when `CASTLE_YTDLP` names
/// one, the sidecar when there is one, a PATH search otherwise — shutil.which's answer, before a spawn
/// turns "not installed" into an OS error string.
pub fn have_yt_dlp() -> bool {
    have("CASTLE_YTDLP", "yt-dlp")
}

fn named(env: &str) -> Option<OsString> {
    std::env::var_os(env).filter(|v| !v.is_empty())
}

fn program(env: &str, name: &str) -> OsString {
    named(env)
        .or_else(|| sidecar(name))
        .unwrap_or_else(|| OsString::from(name))
}

fn have(env: &str, name: &str) -> bool {
    match named(env) {
        Some(p) => Path::new(&p).is_file(),
        None => sidecar(name).is_some() || on_path(name, std::env::var_os("PATH")),
    }
}

/// `name` beside the running binary, when it is there.
fn sidecar(name: &str) -> Option<OsString> {
    let exe = std::env::current_exe().ok()?;
    beside(exe.parent()?, name)
}

fn beside(dir: &Path, name: &str) -> Option<OsString> {
    let p = dir.join(format!("{name}{}", std::env::consts::EXE_SUFFIX));
    p.is_file().then(|| p.into_os_string())
}

/// shutil.which, for one bare name over one PATH value: the platform's
/// list separator (`;` on Windows), and the `.exe` the platform adds.
fn on_path(name: &str, path: Option<OsString>) -> bool {
    let Some(path) = path else { return false };
    let file = format!("{name}{}", std::env::consts::EXE_SUFFIX);
    std::env::split_paths(&path).any(|d| d.join(&file).is_file())
}

/// Path(...).name — the last segment of a name, every separator this
/// platform honours stripped (see the module note). Trailing separators
/// are ignored, so `a/b/` is `b`.
pub fn last_segment(s: &str) -> String {
    let cut = |c: char| c == '/' || c == '\\' || (cfg!(windows) && c == ':');
    s.trim_end_matches(cut)
        .rsplit(cut)
        .next()
        .unwrap_or("")
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_name_keeps_only_its_last_segment_whichever_slash_it_used() {
        assert_eq!(last_segment("vigil"), "vigil");
        assert_eq!(last_segment("../../etc/passwd"), "passwd");
        assert_eq!(last_segment("..\\..\\Windows\\win.ini"), "win.ini");
        assert_eq!(last_segment("C:\\Users\\me\\song.mp3"), "song.mp3");
        assert_eq!(last_segment("mixed/dir\\x.wav"), "x.wav");
        assert_eq!(last_segment("a\\b\\\\"), "b");
        assert_eq!(last_segment("\\"), "");
        // A drive-relative name: a different root on Windows, so the
        // prefix goes there; elsewhere ':' is an ordinary character.
        let want = if cfg!(windows) { "x.mp3" } else { "C:x.mp3" };
        assert_eq!(last_segment("C:x.mp3"), want);
    }

    #[test]
    fn a_program_on_path_is_found_with_the_platforms_suffix() {
        let d = std::env::temp_dir().join(format!("castle-onpath-{}", std::process::id()));
        std::fs::create_dir_all(&d).expect("temp dir");
        let file = format!("_t_castle_tool{}", std::env::consts::EXE_SUFFIX);
        std::fs::write(d.join(&file), b"").expect("fake tool");
        let path = std::env::join_paths([Path::new("/nowhere/_t_dir"), &d]).expect("PATH");
        assert!(on_path("_t_castle_tool", Some(path.clone())));
        assert!(!on_path("_t_castle_absent", Some(path)));
        assert!(!on_path("_t_castle_tool", None));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_sidecar_is_the_file_beside_the_binary_with_the_platforms_suffix() {
        let d = std::env::temp_dir().join(format!("castle-sidecar-{}", std::process::id()));
        std::fs::create_dir_all(&d).expect("temp dir");
        let file = d.join(format!("_t_castle_side{}", std::env::consts::EXE_SUFFIX));
        std::fs::write(&file, b"").expect("fake sidecar");
        assert_eq!(beside(&d, "_t_castle_side"), Some(file.into_os_string()));
        assert_eq!(beside(&d, "_t_castle_absent"), None);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_named_program_wins_and_an_empty_name_is_no_name() {
        // The environment is read, never written: setting a variable here
        // would race every test that spawns ffmpeg in this process.
        match named("CASTLE_FFMPEG") {
            Some(p) => assert_eq!(ffmpeg(), p),
            None => assert_eq!(
                ffmpeg(),
                sidecar("ffmpeg").unwrap_or_else(|| "ffmpeg".into())
            ),
        }
        assert_eq!(program("_T_CASTLE_UNSET_VAR", "tool"), "tool");
        assert!(named("_T_CASTLE_UNSET_VAR").is_none());
    }
}
