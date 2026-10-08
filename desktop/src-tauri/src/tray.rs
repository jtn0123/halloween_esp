//! The ♜ in the menu bar (macOS) / notification area (Windows): the Swift
//! launcher's Start / Open log / Quit, plus Open in browser, updates and —
//! in an app that carries its own tools — Repair, the splash's button for
//! when Castle Radio runs but a tool it needs is broken.

use crate::Servers;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_opener::OpenerExt;

/// The tray's Repair, by name: tools/castle_tools_status.py points the
/// owner at it (tests/test_castle_tools_status.py reads it from here).
pub const REPAIR: &str = "Repair Castle Tools…";
const REPAIR_ASK: &str = "Repair Castle Tools? It sets the tools up again from \
the internet and takes a few minutes. Your songs, your show and your settings \
are kept.";

/// One line of the menu.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Entry {
    Item(&'static str, &'static str),
    Separator,
}

/// What a menu id does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Action {
    Show,
    Start,
    Browser,
    Desk,
    Log,
    Repair,
    Updates,
    Quit,
}

/// The menu, top to bottom. Repair only where Setup::repairable: a dev run
/// has no tools of its own to set up again.
fn entries(repairable: bool) -> Vec<Entry> {
    let mut menu = vec![
        Entry::Item("show", "Show Castle Tools"),
        Entry::Item("start", "Start"),
        Entry::Item("browser", "Open in browser"),
        Entry::Item("desk", "Open the light desk in browser"),
        Entry::Item("log", "Open log"),
    ];
    if repairable {
        menu.push(Entry::Item("repair", REPAIR));
    }
    menu.extend([
        Entry::Separator,
        Entry::Item("updates", "Check for updates…"),
        Entry::Separator,
        Entry::Item("quit", "Quit Castle Tools"),
    ]);
    menu
}

fn action(id: &str) -> Option<Action> {
    Some(match id {
        "show" => Action::Show,
        "start" => Action::Start,
        "browser" => Action::Browser,
        "desk" => Action::Desk,
        "log" => Action::Log,
        "repair" => Action::Repair,
        "updates" => Action::Updates,
        "quit" => Action::Quit,
        _ => return None,
    })
}

pub fn build(app: &AppHandle) -> tauri::Result<TrayIcon> {
    let menu = Menu::new(app)?;
    let repairable = app.state::<Servers>().setup.repairable();
    for entry in entries(repairable) {
        match entry {
            Entry::Item(id, text) => {
                menu.append(&MenuItem::with_id(app, id, text, true, None::<&str>)?)?
            }
            Entry::Separator => menu.append(&PredefinedMenuItem::separator(app)?)?,
        }
    }
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
    let Some(action) = action(event.id().as_ref()) else {
        return;
    };
    match action {
        Action::Show => show_main(app),
        Action::Start => servers.start(),
        Action::Browser => {
            let _ = app.opener().open_url(servers.radio.url(), None::<&str>);
        }
        Action::Desk => {
            let _ = app.opener().open_url(servers.desk.url(), None::<&str>);
        }
        Action::Log => {
            let _ = app
                .opener()
                .open_path(servers.radio.log_path().to_string_lossy(), None::<&str>);
        }
        Action::Repair => {
            let app = app.clone();
            std::thread::spawn(move || repair(&app));
        }
        Action::Updates => {
            let app = app.clone();
            std::thread::spawn(move || crate::updater::check(&app, true));
        }
        Action::Quit => app.exit(0),
    }
}

/// Asked first — it takes minutes and the internet — then the window shows
/// the splash, which follows the setup and opens Castle Radio when it is
/// done. Blocking (the dialog): a worker thread's, never the main thread's.
fn repair(app: &AppHandle) {
    let yes = app
        .dialog()
        .message(REPAIR_ASK)
        .title("Castle Tools")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Repair".into(),
            "Cancel".into(),
        ))
        .blocking_show();
    if yes {
        crate::show_splash(app);
        app.state::<Servers>().repair();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ids(repairable: bool) -> Vec<&'static str> {
        entries(repairable)
            .into_iter()
            .filter_map(|e| match e {
                Entry::Item(id, _) => Some(id),
                Entry::Separator => None,
            })
            .collect()
    }

    #[test]
    fn repair_is_offered_only_by_an_app_with_its_own_tools() {
        assert!(entries(true).contains(&Entry::Item("repair", REPAIR)));
        assert!(!ids(false).contains(&"repair"));
        assert_eq!(ids(true).len(), ids(false).len() + 1);
    }

    #[test]
    fn every_line_of_the_menu_does_something_and_only_one_thing() {
        let all = ids(true);
        for id in &all {
            assert!(action(id).is_some(), "{id} is on the menu but does nothing");
        }
        let mut unique = all.clone();
        unique.sort_unstable();
        unique.dedup();
        assert_eq!(unique.len(), all.len());
        assert_eq!(action("anything else"), None);
        assert_eq!(action("repair"), Some(Action::Repair));
    }
}
