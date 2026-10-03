//! The castle key, from the desk's side — `/studio/castle-key` (docs/API.md).
//!
//! Firmware v5.74 lets the owner lock a castle's writes behind a key; the
//! relay sends whatever key the store holds for the castle it talks to
//! (studio_relay::key_for). This is how the desk puts one IN the store:
//!
//! - GET says which castle, whether a key is remembered for it and whether
//!   CASTLE_KEY pins one — never the key itself.
//! - POST `{"action": "use"|"set"|"clear", "key": "…"}`:
//!   `use` tries the key and remembers it if it opens the door — a
//!   `POST /api/key` with nothing to change answers 400 to the right key (or
//!   to any key on a castle that has none) and 401 to a wrong one, so it
//!   checks without changing anything; `set` is `/api/key?new=<key>` sent
//!   with the key held now, remembered on the castle's 200; `clear` is
//!   `/api/key?clear=1`, forgotten on the 200.
//!
//! The store is the inventory (studio_relay::devices_file) and its one
//! writer is tools/castle_keys.py, spawned with the key on stdin: argv is
//! readable by every user on the machine. With CASTLE_KEY set the store
//! cannot follow a change — the variable wins — so every action refuses
//! rather than leave a castle locked behind a key nothing sends. No reply
//! and no log line carries a key: the errors name the castle and the rule.

use crate::bridge::{self, CallFault};
use crate::hosts;
use crate::httpd::{Reply, Request};
use crate::jsonio::Json;
use crate::studio::App;
use crate::studio_proc::{Timed, py, run_input};
use crate::studio_relay::{self as rl, TIMEOUT_S};
use crate::studio_routes::{bad_request, jerr, json_body};

pub const ROUTE: &str = "/studio/castle-key";

const PINNED: &str = "CASTLE_KEY sets this castle's key for the whole session \
     (settings.json castle_key in the app) — change it there";
const BAD_KEY: &str = "a castle key is 1-64 printable characters, no spaces";
const WRONG_KEY: &str = "that is not this castle's key";
const HELD_WRONG: &str = "This castle has a key — enter it in Settings";
const OLD_FIRMWARE: &str = "this castle's firmware has no key (v5.74 and newer do)";

/// Which castle, and what the store knows about it.
fn state(app: &App, host: &str) -> Json {
    let toml = std::fs::read_to_string(rl::devices_file(app)).unwrap_or_default();
    Json::Obj(vec![
        ("ok".into(), Json::Bool(true)),
        ("host".into(), Json::Str(host.into())),
        (
            "remembered".into(),
            Json::Bool(!hosts::castle_key(Some(host), None, &toml).is_empty()),
        ),
        ("pinned".into(), Json::Bool(hosts::env_key().is_some())),
    ])
}

pub fn get(app: &App) -> Reply {
    match rl::castle_host(app) {
        Some(host) => Reply::Json(state(app, &host), 200),
        None => jerr("no castle configured", 409),
    }
}

pub fn post(app: &App, req: &Request) -> Reply {
    let body = match json_body(&req.body) {
        Ok(v) => v,
        Err(e) => return bad_request(&e),
    };
    let action = body.str_or("action", "");
    let key = body.str_or("key", "");
    let Some(host) = rl::castle_host(app) else {
        return jerr("no castle configured", 409);
    };
    if hosts::env_key().is_some() {
        return jerr(PINNED, 409);
    }
    let (target, send) = match action.as_str() {
        "use" | "set" if !hosts::valid_key(&key) => return jerr(BAD_KEY, 400),
        "use" => ("/api/key".to_string(), key.clone()),
        "set" => (
            format!("/api/key?new={}", bridge::encode_query(&key)),
            rl::key_for(app, &host),
        ),
        "clear" => ("/api/key?clear=1".to_string(), rl::key_for(app, &host)),
        _ => return jerr("action is use, set or clear", 400),
    };
    let r = match bridge::call(
        &rl::with_port(&host),
        "POST",
        &target,
        b"",
        &send,
        TIMEOUT_S,
        TIMEOUT_S,
    ) {
        Ok(r) => r,
        Err(CallFault::Unreachable(_)) => return jerr("castle not reachable", 502),
        Err(CallFault::Stalled(_)) => return jerr("castle did not answer in time", 504),
    };
    let opened = if action == "use" { 400 } else { 200 };
    match r.code {
        c if c == opened => {}
        401 if action == "use" => return jerr(WRONG_KEY, 401),
        401 => return jerr(HELD_WRONG, 401),
        404 => return jerr(OLD_FIRMWARE, 409),
        400 => return jerr(String::from_utf8_lossy(&r.body).trim(), 400),
        c => return jerr(&format!("castle answered {c}"), 502),
    }
    let kept = if action == "clear" {
        store(app, &host, None)
    } else {
        store(app, &host, Some(&key))
    };
    match kept {
        Ok(()) => Reply::Json(state(app, &host), 200),
        // The castle has already changed: say so, or the owner is left
        // guessing which key the castle now has.
        Err(why) => jerr(
            &format!("the castle took it, but this computer could not remember it: {why}"),
            500,
        ),
    }
}

/// Remember (`Some`) or forget (`None`) the key for `host`, through the
/// store's one writer.
fn store(app: &App, host: &str, key: Option<&str>) -> Result<(), String> {
    let mut cmd = std::process::Command::new(py(&app.root));
    cmd.arg(app.root.join("tools").join("castle_keys.py"))
        .arg(if key.is_some() { "remember" } else { "forget" })
        .arg(host)
        .env("CASTLE_DEVICES", rl::devices_file(app));
    match run_input(cmd, &format!("{}\n", key.unwrap_or("")), 30) {
        Timed::Done(true, _, _) => Ok(()),
        Timed::Done(false, _, err) => Err(err
            .lines()
            .rev()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("tools/castle_keys.py failed")
            .trim()
            .to_string()),
        Timed::Out => Err("tools/castle_keys.py did not finish".into()),
    }
}

/// A request target as the studio's access log may print it. The relay
/// forwards `/api/key` (studio_relay::KNOWN_API), and `?new=` carries a key
/// in the query: the line keeps the route and drops the rest. Nothing the
/// desk sends goes that way — it POSTs the key in a body to [`ROUTE`] — but
/// a log is read by whoever has the terminal, so the rule is the log's.
pub fn loggable(target: &str) -> std::borrow::Cow<'_, str> {
    match target.split_once('?') {
        Some((path, _)) if path == "/api/key" => format!("{path}?…").into(),
        _ => target.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::loggable;

    #[test]
    fn a_key_in_a_relayed_query_never_reaches_the_access_log() {
        assert_eq!(loggable("/api/key?new=s3cret"), "/api/key?…");
        assert_eq!(loggable("/api/key?clear=1"), "/api/key?…");
        assert_eq!(loggable("/api/key"), "/api/key");
        assert_eq!(loggable("/api/volume?v=40"), "/api/volume?v=40");
        assert_eq!(loggable("/api/keys?x=1"), "/api/keys?x=1");
    }
}
