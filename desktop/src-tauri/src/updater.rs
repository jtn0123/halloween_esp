//! App updates from the public GitHub Releases — on launch and once a day,
//! always asked, never installed silently.
//!
//! The endpoint is `releases/latest/download/latest.json`; GitHub's
//! "latest" never names a pre-release, so this is the stable channel. An
//! owner who opted in to pre-releases (channel.rs — hidden, on no page) is
//! pointed at the newest release's own latest.json instead. Each check is
//! one unauthenticated request, far inside the 60-an-hour limit.
//!
//! Until a real minisign public key replaces the placeholder in
//! tauri.conf.json the checks are skipped (and "Check for updates" says
//! so): an update the app cannot verify is not one it should offer.

use crate::{channel, probe, release, Servers};
use std::time::Duration;
use tauri::{AppHandle, Manager, Url};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// The marker the shipped tauri.conf.json carries until the owner runs
/// `cargo tauri signer generate` (desktop/README.md, "Updates").
pub const PUBKEY_PLACEHOLDER: &str = "CASTLE_TOOLS_UPDATER_PUBKEY_PLACEHOLDER";
const FIRST_CHECK: Duration = Duration::from_secs(20);
const DAILY: Duration = Duration::from_secs(24 * 60 * 60);

fn configured(app: &AppHandle) -> bool {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|u| u.get("pubkey"))
        .and_then(|k| k.as_str())
        .is_some_and(|k| !k.is_empty() && k != PUBKEY_PLACEHOLDER)
}

/// The launch check (after the server has had a moment), then one a day.
pub fn schedule(app: AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(FIRST_CHECK);
        loop {
            check(&app, false);
            std::thread::sleep(DAILY);
        }
    });
}

fn say(app: &AppHandle, kind: MessageDialogKind, text: &str) {
    app.dialog()
        .message(text)
        .title("Castle Tools")
        .kind(kind)
        .blocking_show();
}

fn ask(app: &AppHandle, text: &str) -> bool {
    app.dialog()
        .message(text)
        .title("Castle Tools update")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install and restart".into(),
            "Later".into(),
        ))
        .blocking_show()
}

/// The channel this check is on, and its latest.json (channel::endpoint):
/// opted in, Castle Radio is asked which release that is.
fn where_to_look(app: &AppHandle) -> (bool, (String, Option<String>)) {
    let servers = app.try_state::<Servers>();
    let setting = servers
        .as_ref()
        .is_some_and(|s| s.radio.settings().prerelease);
    let prerelease = channel::opted_in_now(setting);
    let answer = match (&servers, prerelease) {
        (Some(s), true) => probe::get_json(s.radio.port(), channel::ROUTE, channel::WAIT),
        _ => None,
    };
    (prerelease, channel::endpoint(prerelease, answer.as_ref()))
}

/// Blocking: call from a worker thread, never the main thread (the dialogs
/// wait for an answer). `manual` is "Check for updates" from the tray — it
/// reports every outcome; the scheduled check speaks only when there is
/// something to install.
pub fn check(app: &AppHandle, manual: bool) {
    let log = |line: &str| {
        if let Some(servers) = app.try_state::<Servers>() {
            servers.radio.log_line(line);
        }
    };
    if !configured(app) {
        log("update check skipped: this build has no updater public key");
        if manual {
            say(app, MessageDialogKind::Info, "This build of Castle Tools was made without update signing, so it cannot check for updates.");
        }
        return;
    }
    let (prerelease, endpoint) = where_to_look(app);
    let endpoint = match endpoint {
        (url, Some(note)) => {
            log(&note);
            url
        }
        (url, None) => url,
    };
    let found = tauri::async_runtime::block_on(async {
        let url = Url::parse(&endpoint).map_err(|e| e.to_string())?;
        app.updater_builder()
            .endpoints(vec![url])
            .and_then(|b| b.build())
            .map_err(|e| e.to_string())?
            .check()
            .await
            .map_err(|e| e.to_string())
    });
    let update = match found {
        Ok(Some(update)) => update,
        Ok(None) => {
            log("update check: up to date");
            if manual {
                let version = app.package_info().version.to_string();
                say(
                    app,
                    MessageDialogKind::Info,
                    &format!("Castle Tools {version} is the latest version."),
                );
            }
            return;
        }
        Err(e) => {
            log(&format!("update check failed: {e}"));
            if manual {
                say(
                    app,
                    MessageDialogKind::Warning,
                    &format!("Could not check for updates: {e}"),
                );
            }
            return;
        }
    };
    // latest.json is published by the release workflow, but the buyer's
    // channel is stable tags only unless they opted in (PRODUCTION-TODO §9):
    // a version the channel does not take, or the release contract cannot
    // name, is not offered, whatever the file says.
    if !channel::accepts(&release::tag(&update.version), prerelease) {
        log(&format!(
            "update {} ignored: not a release tag this channel takes",
            update.version
        ));
        if manual {
            let version = app.package_info().version.to_string();
            say(
                app,
                MessageDialogKind::Info,
                &format!("Castle Tools {version} is the latest stable version."),
            );
        }
        return;
    }
    log(&format!(
        "update {} available (running {})",
        update.version, update.current_version
    ));
    let question = format!(
        "Castle Tools {} is available (you have {}). Install it now? The app restarts; your songs stay where they are.",
        update.version, update.current_version
    );
    if !ask(app, &question) {
        log("update postponed by the owner");
        return;
    }
    // The installer replaces the files the servers run from; stop them first.
    if let Some(servers) = app.try_state::<Servers>() {
        servers.stop();
    }
    let installed = tauri::async_runtime::block_on(update.download_and_install(|_, _| {}, || {}));
    match installed {
        Ok(()) => {
            log("update installed; restarting");
            app.restart();
        }
        Err(e) => {
            log(&format!("update failed: {e}"));
            say(
                app,
                MessageDialogKind::Error,
                &format!("The update could not be installed: {e}"),
            );
            if let Some(servers) = app.try_state::<Servers>() {
                servers.start();
            }
        }
    }
}
