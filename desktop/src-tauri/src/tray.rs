//! The ♜ in the menu bar (macOS) / notification area (Windows): the Swift
//! launcher's Start / Open log / Quit, plus Open in browser and updates.

use crate::Servers;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

pub fn build(app: &AppHandle) -> tauri::Result<TrayIcon> {
    let item = |id: &str, text: &str| MenuItem::with_id(app, id, text, true, None::<&str>);
    let menu = Menu::with_items(
        app,
        &[
            &item("show", "Show Castle Tools")?,
            &item("start", "Start")?,
            &item("browser", "Open in browser")?,
            &item("desk", "Open the light desk in browser")?,
            &item("log", "Open log")?,
            &PredefinedMenuItem::separator(app)?,
            &item("updates", "Check for updates…")?,
            &PredefinedMenuItem::separator(app)?,
            &item("quit", "Quit Castle Tools")?,
        ],
    )?;
    let mut tray = TrayIconBuilder::with_id("castle")
        .tooltip("Castle Tools")
        .menu(&menu)
        .on_menu_event(on_menu)
        .on_tray_icon_event(on_click);
    if cfg!(target_os = "macos") {
        // The glyph itself, as the Swift app drew it: a text title follows
        // the menu bar's light/dark appearance with no template image.
        tray = tray.title("♜").show_menu_on_left_click(true);
    } else if let Some(icon) = app.default_window_icon() {
        // Windows' notification area takes an image only; a left click
        // opens the window and the right click the menu, as Windows does.
        tray = tray.icon(icon.clone()).show_menu_on_left_click(false);
    }
    tray.build(app)
}

pub fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn on_click(tray: &TrayIcon, event: TrayIconEvent) {
    if cfg!(target_os = "macos") {
        return;
    }
    if let TrayIconEvent::Click {
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
        ..
    } = event
    {
        show_main(tray.app_handle());
    }
}

fn on_menu(app: &AppHandle, event: MenuEvent) {
    let servers = app.state::<Servers>();
    match event.id().as_ref() {
        "show" => show_main(app),
        "start" => servers.start(),
        "browser" => {
            let _ = app.opener().open_url(servers.radio.url(), None::<&str>);
        }
        "desk" => {
            let _ = app.opener().open_url(servers.desk.url(), None::<&str>);
        }
        "log" => {
            let _ = app
                .opener()
                .open_path(servers.radio.log_path().to_string_lossy(), None::<&str>);
        }
        "updates" => {
            let app = app.clone();
            std::thread::spawn(move || crate::updater::check(&app, true));
        }
        "quit" => app.exit(0),
        _ => {}
    }
}
