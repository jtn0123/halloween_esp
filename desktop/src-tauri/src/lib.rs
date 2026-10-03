//! Castle Tools: a window onto Castle Radio (the local player + importer on
//! 127.0.0.1:8871), the cue desk studio beside it, the ♜ tray, the
//! `castle-tools://start` handler and the updater. The servers stay the ones
//! they always were — Python's Castle Radio and castle-core's `studio` bin —
//! run as sidecars: this app starts them, watches them and stops them.
//! Architecture: desktop/README.md.

mod channel;
mod child;
mod deeplink;
mod logfile;
mod probe;
mod release;
mod runtime;
mod service;
mod settings;
mod signals;
mod supervisor;
mod tray;
mod updater;

use serde::Serialize;
use service::Service;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};
use supervisor::{Config, Events, Status, Supervisor};
use tauri::{AppHandle, Manager, RunEvent, Url, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_opener::OpenerExt;

/// Both supervisors, as Tauri state: the window follows Castle Radio; the
/// studio runs beside it for the light desk and the rebuild/publish work.
pub struct Servers {
    pub radio: Arc<Supervisor>,
    pub desk: Arc<Supervisor>,
}

impl Servers {
    pub fn start(&self) {
        self.radio.start();
        self.desk.start();
    }

    pub fn stop(&self) {
        self.radio.stop();
        self.desk.stop();
    }
}

/// Moves the main window between the local splash page and Castle Radio.
struct Navigator {
    app: AppHandle,
    splash: OnceLock<Url>,
}

impl Events for Navigator {
    fn ready(&self, url: &str) {
        if let (Some(window), Ok(url)) = (self.app.get_webview_window("main"), url.parse::<Url>()) {
            let _ = window.navigate(url);
        }
    }

    fn failed(&self, _message: &str) {
        let (Some(window), Some(splash)) = (self.app.get_webview_window("main"), self.splash.get())
        else {
            return;
        };
        // The splash polls the status itself; only leave a dead page.
        if window
            .url()
            .map(|u| u.origin() != splash.origin())
            .unwrap_or(true)
        {
            let _ = window.navigate(splash.clone());
        }
    }
}

/// The studio has no window of its own; its outcome is a log line, and the
/// splash shows it under Castle Radio's.
struct DeskEvents;

impl Events for DeskEvents {
    fn ready(&self, _url: &str) {}
    fn failed(&self, _message: &str) {}
}

#[derive(Serialize)]
struct Statuses {
    radio: Status,
    desk: Status,
}

#[tauri::command]
fn castle_status(servers: tauri::State<'_, Servers>) -> Statuses {
    Statuses {
        radio: servers.radio.status(),
        desk: servers.desk.status(),
    }
}

#[tauri::command]
fn castle_start(servers: tauri::State<'_, Servers>) {
    servers.start();
}

#[tauri::command]
fn castle_open_log(app: AppHandle, servers: tauri::State<'_, Servers>) -> Result<(), String> {
    app.opener()
        .open_path(servers.radio.log_path().to_string_lossy(), None::<&str>)
        .map_err(|e| e.to_string())
}

/// This build's version and the Release asset names it belongs to.
#[tauri::command]
fn castle_release(app: AppHandle) -> release::Assets {
    release::assets(&app.package_info().version.to_string())
}

fn supervisor(
    service: Service,
    resource_dir: Option<PathBuf>,
    app_data: PathBuf,
    settings: settings::Settings,
    log: &Arc<logfile::LogFile>,
    events: Box<dyn Events>,
) -> Arc<Supervisor> {
    let config = Config {
        service,
        port: service.port(&|k| std::env::var_os(k)),
        resource_dir,
        app_data,
        settings,
    };
    Supervisor::new(config, Arc::clone(log), events)
}

fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();
    let paths = app.path();
    let log = Arc::new(logfile::LogFile::open(
        &paths.app_log_dir()?,
        "castle-tools.log",
    )?);
    log.line(&format!(
        "Castle Tools {} starting",
        app.package_info().version
    ));
    let config_dir = paths.app_config_dir()?;
    let (settings, warning) = settings::load(&config_dir);
    if let Some(warning) = warning {
        log.line(&format!("settings ignored: {warning}"));
    }
    let navigator = Navigator {
        app: handle.clone(),
        splash: OnceLock::new(),
    };
    if let Some(url) = app.get_webview_window("main").and_then(|w| w.url().ok()) {
        let _ = navigator.splash.set(url);
    }
    let resource_dir = paths.resource_dir().ok();
    let app_data = paths.app_data_dir()?;
    let desk = supervisor(
        Service::Studio,
        resource_dir.clone(),
        app_data.clone(),
        settings.clone(),
        &log,
        Box::new(DeskEvents),
    );
    let sup = supervisor(
        Service::Radio,
        resource_dir,
        app_data,
        settings,
        &log,
        Box::new(navigator),
    );
    app.manage(Servers {
        radio: Arc::clone(&sup),
        desk: Arc::clone(&desk),
    });
    tray::build(&handle)?;

    // Registered by the installer on Windows and by Info.plist on macOS; a
    // dev run registers itself so the link can be tried without installing.
    #[cfg(any(windows, target_os = "linux"))]
    if cfg!(debug_assertions) {
        if let Err(e) = app.deep_link().register_all() {
            log.line(&format!("deep link registration failed: {e}"));
        }
    }
    let on_link = Arc::clone(&sup);
    let on_desk = Arc::clone(&desk);
    app.deep_link().on_open_url(move |event| {
        for url in event.urls() {
            if deeplink::is_start(url.as_str()) {
                on_link.start();
                on_desk.start();
            } else {
                on_link.log_line(&format!(
                    "ignored link {:?}: only {} is honoured",
                    url.as_str(),
                    deeplink::START
                ));
            }
        }
    });
    // Launched BY the castle page's link: start quietly and leave the
    // browser in front, as the Swift launcher did. Launched by the owner:
    // show the window.
    let by_link = app
        .deep_link()
        .get_current()
        .ok()
        .flatten()
        .is_some_and(|urls| urls.iter().any(|u| deeplink::is_start(u.as_str())));
    if !by_link {
        tray::show_main(&handle);
    }
    sup.start();
    desk.start();
    signals::install(handle.clone());
    updater::schedule(handle);
    Ok(())
}

pub fn run() {
    let mut builder = tauri::Builder::default();
    #[cfg(desktop)]
    {
        // First, as the plugin requires: a second launch (a double-click, or
        // Windows delivering a castle-tools:// link to a new process) is
        // handed to this one. The link itself arrives via the deep-link
        // plugin's event; a bare relaunch just shows the window.
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            if !deeplink::in_args(&argv) {
                tray::show_main(app);
            }
        }));
    }
    let app = builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            castle_status,
            castle_start,
            castle_open_log,
            castle_release
        ])
        .setup(setup)
        .on_window_event(|window, event| {
            // Closing the window keeps the server and the tray; Quit ends both.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Castle Tools");
    app.run(|app, event| match event {
        RunEvent::Exit => {
            if let Some(servers) = app.try_state::<Servers>() {
                servers.stop();
            }
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => tray::show_main(app),
        _ => {}
    });
}
