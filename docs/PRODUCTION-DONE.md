# Production — done

Sections of docs/PRODUCTION-TODO.md whose every item is done or decided,
moved here whole on 2026-10-02 when the TODO reached the 500-line rule
(§4.2, §4.3 and §7 followed on 2026-10-03).
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

### 4.2 Python (`tools/`, `demo/castle-radio/`)
- [x] `tools/manifest.py:30` `fcntl.flock` → cross-platform lock (`msvcrt.locking`
      on Windows, or a lock-file with `os.open(O_CREAT|O_EXCL)`), shared helper.
- [x] `tools/progress_process.py:16,31` and `demo/castle-radio/job_progress.py:107`
      `start_new_session` + `os.killpg` → Windows `CREATE_NEW_PROCESS_GROUP` +
      `taskkill /T /F` (or a job object via ctypes). One helper, both callers.
- [x] Every hardcoded `.venv/bin/python` / `bin/` path → `sys.executable` or
      a resolver that knows `Scripts\`.
- [x] `os.replace` atomic-write paths: Windows fails if the target is open —
      retry loop around the rename.
- [x] Temp files: `NamedTemporaryFile(delete=False)` pattern where a child
      process must reopen the file (Windows can't reopen an open temp file).
- [x] Encoding: every `open()` passes `encoding="utf-8"` (Windows default is
      cp1252). Add a ruff rule (`PLW1514`) so it stays that way.
- [x] Ports: Windows firewall prompt on first bind — bind `127.0.0.1` only
      (already the default) so the prompt does not appear. Castle Radio and
      the studio, started as the app and the launchers start them, are
      probed from this machine's LAN address by tests/test_loopback_rs.py;
      `--lan` stays the studio's opt-in.
- [x] `demo/castle-radio/desktop_tools.py` + `tools/register_castle_launcher.py`:
      Mac-only branches stay, behind a platform check, until the Tauri app
      replaces them. Registration says "not on this platform" elsewhere;
      `/radio/tools` names each platform's installer and offers website
      startup on macOS only (tests across darwin, win32 and linux).

### 4.3 CI
- [x] `windows-latest` job: `cargo build --release` + `cargo test` for `core/`.
- [x] `windows-latest` job: `make test`-equivalent Python suite (no Make on
      Windows — a `tools/run_checks.py` the Makefile also calls). Green and
      blocking since 2026-10-01.
- [x] `macos-14` job: same, so Apple Silicon is tested in CI, not just here.
- [x] Tests that assume POSIX (`/bin/sh`, `/tmp`, chmod) get a Windows path
      or a portable rewrite — never a skip (CLAUDE.md rule). The C++ card
      harnesses build with MinGW's g++ there (`tests/cxx_compiler.py`).

## 7. Desktop-tool stability (P1)

- [x] Import pipeline failure messages written for an owner, not a developer
      (`studio_reason.rs` already explains errors — audit the wording).
      2026-10-02: one sentence, what happened then what to do, from
      `tools/import_reason.py` and its word-for-word Rust copy
      (`core/src/studio_reason_words.rs`, docs/PARITY.md). The importer,
      the splitter, the studio and Castle Radio all end that way, with the
      tools' own output behind Details. Cancel still reads "Cancelled".
- [x] Castle offline / wrong address: one clear state, one "find my castle"
      action (mDNS browse + manual IP). Castle Radio's Find my castle
      (`tools/castle_find.py`, stdlib) writes the per-user store; the
      studio, and so the desk's chip, follow it.
- [x] Sync interrupted mid-push: resumable or safely retried; card never left
      with a half-written `show.man`. `sd_sync` and Castle Radio send the
      show before what names it, record each verified file as it lands and
      skip it on the retry; `tests/test_sd_sync_resume.py` and the radio's
      `test_sync_resume.py` cut real pushes on the emulator (`drop_after`).
- [x] Disk-full, unsupported file type, 2-hour file, non-ASCII filenames
      (Windows + mac), file on a network drive.
      2026-10-02: each has a test (`tests/test_import_edges.py`,
      `demo/castle-radio/test_radio_failures.py`) and a sentence. Songs are
      limited to 15 minutes because analysis peaks near 250 MB a minute.
      desktop/README.md "What an import can take". A real Windows machine
      and a real network share are still the §4.4 hands-on pass.
- [x] Firmware/app version handshake: the app refuses (with a message) to push
      a show format the castle's firmware cannot read. Done: §9's show/card
      format item — one table, `tools/fw_formats.py`.
- [x] Crash reporting: a local "copy diagnostics" button (no telemetry).
      Castle Radio's help card: the castle's v5.75 report plus app version,
      tools, jobs and log tail — paths cut, no key. The desk links `/owner`.
