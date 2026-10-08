//! SIGTERM / SIGINT / SIGHUP become an orderly Quit on Unix.
//!
//! The server runs in its own process group (child.rs), so a signal to the
//! app does not reach it; without this a `kill`, a Ctrl-C in a dev terminal
//! or a closed terminal would leave Castle Radio running with no owner. The
//! handler only stores a flag (async-signal-safe); a watcher thread turns it
//! into `app.exit(0)`, which runs the same stop as the tray's Quit.
//! Windows needs none of this: the job object kills the tree when the app
//! goes, however it goes.

#[cfg(unix)]
pub fn install(app: tauri::AppHandle) {
    use std::sync::atomic::{AtomicBool, Ordering};
    static QUIT: AtomicBool = AtomicBool::new(false);

    extern "C" fn on_signal(_: libc::c_int) {
        QUIT.store(true, Ordering::SeqCst);
    }

    for sig in [libc::SIGTERM, libc::SIGINT, libc::SIGHUP] {
        // SAFETY: the handler only performs an atomic store.
        unsafe { libc::signal(sig, on_signal as *const () as libc::sighandler_t) };
    }
    std::thread::spawn(move || loop {
        if QUIT.load(Ordering::SeqCst) {
            app.exit(0);
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    });
}

#[cfg(not(unix))]
pub fn install(_app: tauri::AppHandle) {}
