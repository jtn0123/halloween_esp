# Production — done

Sections of docs/PRODUCTION-TODO.md whose every item is done or decided,
moved here whole on 2026-10-02 when the TODO reached the 500-line rule.
They keep the TODO's numbering, so a `PRODUCTION-TODO §4.1` citation
elsewhere still finds its section — here, behind the TODO's pointer.

## 0. Scope decisions (answer first — they size everything below)

- [x] **(decide)** What does the buyer DO? Unknown — plan for all three:
      run the show, add songs, edit scenes (2026-09-30).
- [x] **(decide)** Deadline: working before Halloween 2026 (~4 weeks from
      2026-09-30). Sections 1–3 + option A (section 6) are the Halloween
      target; the Tauri app (section 5) and signing most likely land after.
- [x] **(decide)** Buyer's computer: unknown, most likely Windows — support
      BOTH (2026-09-30). Windows is the primary test target for section 4+.
- [x] **(decide)** Handover in person (2026-09-30): you set up their Wi-Fi
      and install the desktop tools on their computer yourself. Captive
      portal is still wanted for "router changed" later, but is no longer a
      day-one blocker.
- [x] **(decide)** Updates (2026-09-30): the desktop app watches
      `github.com/jtn0123/halloween_esp` Releases (public — no token) and
      auto-updates itself, and offers the matching firmware + card format to
      the castle. See section 9.
- [x] **(decide)** Hardware (2026-09-30): carrier v3.3a OR a follow-on v3.4,
      with the 4 MB flash / 2 MB PSRAM S3 (the Feather #5477, today's build)
      ONLY for now. 16 MB / 4 MB is deferred — 1.5 keeps the door open
      without building it. PSU and case still open.
- [x] **(decide)** Security (2026-09-30): an OPTIONAL password, OFF by
      default — see 1.6. With it off, behaviour is today's.
- [x] **(decide)** Signing (2026-09-30): Windows unsigned. macOS unsigned,
      downloaded from GitHub Releases — see 5.4 for what that costs the
      buyer on first launch.
- [x] **(decide)** Windows test machine (2026-09-30): you have one; a later
      Opus agent session runs the Windows checklist on it (section 4.4).
- [x] **(decide)** Songs (2026-09-30): the card ships with NO songs. The
      buyer adds their own with the app's built-in downloader (URL import,
      yt-dlp) and file import. What they download is theirs to answer for.
- [x] **(decide)** Vocal separation (Demucs/PyTorch): leaning ALWAYS
      INCLUDE (2026-09-30). See 5.2 for the Windows speed plan.

## 4. Cross-platform core (the done part)

### 4.1 Rust (`core/`) — does not compile on Windows today
- [x] `core/src/manifest.rs:15` — hand-declared `flock` FFI → `std::fs::File::lock`
      (stable since Rust 1.89; bump `rust-version` from 1.88). Keeps zero deps.
- [x] `core/src/bin/studio.rs:146` `restart_self()` uses Unix `exec` →
      `#[cfg(windows)]` spawn-then-exit (the PID changes; check whoever
      relies on "PID kept").
- [x] `core/src/bin/studio.rs:222` `bind_reuse` raw `socket/setsockopt` with a
      macOS-only `so` module → `#[cfg(windows)]` plain `TcpListener::bind`
      (Windows' SO_REUSEADDR means something else — do NOT set it there).
- [x] `core/src/studio_proc.rs:29` + `studio_jobs.rs:24` `own_group`/`kill_group`
      (process groups so yt-dlp→ffmpeg grandchildren die) → Windows Job
      Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Needs a small FFI
      block or the `windows-sys` crate (decide: keep zero-dep or not).
- [x] `core/src/studio_scenes.rs:288` `PermissionsExt` — cfg-gate.
- [x] `/bin/sh` in `studio_proc.rs` / `studio_reap.rs` tests → cfg(unix) or
      use a cross-platform child (e.g. the test binary re-invoking itself).
- [x] `studio_proc.rs` `py()` looks for `.venv/bin/python` → also
      `.venv\Scripts\python.exe`, and a bundled interpreter path (section 5).
- [x] Paths: audit for `/`-joined strings, `/tmp`, and `basenames()` that
      split on `/` only (`studio_reason.rs`). Windows paths use `\` and `C:`.
- [x] ffmpeg/yt-dlp lookup: honour a `CASTLE_FFMPEG` / bundled sidecar path
      before `PATH`; `.exe` suffix on Windows.
- [x] Line endings: anything that writes cue/manifest files writes `\n`
      explicitly; `.gitattributes` pins LF for generated card files.
