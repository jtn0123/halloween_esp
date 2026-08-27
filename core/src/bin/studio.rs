//! The cue desk studio, served from castle-core — tools/studio.py's twin.
//!
//!     studio [PORT] [--lan] [--localhost]
//!
//! Binds 127.0.0.1 unless --lan (the server has no auth by accepted
//! design; see the project notes). Port 0 binds an ephemeral port and the
//! banner names the real one, which is how the parity tests find it.
//! tests/test_studio_rust.py drives this and the Python studio side by
//! side and holds the answers equal.

use std::net::{TcpListener, TcpStream};
use std::sync::Arc;

use castle_core::httpd::{deliver, respond_json, scrub, Conn, Reply};
use castle_core::jsonio::Json;
use castle_core::studio::repo_root;
use castle_core::studio::App;
use castle_core::studio_routes::handle;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let port: u16 = args
        .iter()
        .find(|a| !a.starts_with("--"))
        .and_then(|a| a.parse().ok())
        .unwrap_or(8765);
    let host = if args.iter().any(|a| a == "--lan") {
        "0.0.0.0"
    } else {
        "127.0.0.1"
    };
    let app = Arc::new(App::new(repo_root()));
    let _ = std::fs::create_dir(&app.tracks);
    let listener = match TcpListener::bind((host, port)) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("studio: cannot bind {host}:{port}: {e}");
            std::process::exit(1);
        }
    };
    let actual = listener.local_addr().map(|a| a.port()).unwrap_or(port);
    println!("cue desk studio  ->  http://127.0.0.1:{actual}");
    if host == "0.0.0.0" {
        println!("  (OPEN TO YOUR LAN — anyone on the WiFi can edit the show)");
    } else {
        println!("  (this Mac only — pass --lan to reach it from your phone)");
    }
    println!("  serving the previewer with track management enabled");
    println!("  ctrl-c to stop");
    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        let app = Arc::clone(&app);
        std::thread::spawn(move || conn_loop(&app, stream));
    }
}

fn conn_loop(app: &Arc<App>, stream: TcpStream) {
    let mut conn = Conn::new(stream);
    loop {
        match conn.read_request() {
            Ok(None) => break,
            Err(msg) => {
                // The error boundary: a client mistake is a 400 and a
                // closed connection, never a dead socket.
                let body = Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    ("error".into(), Json::Str(msg)),
                ]);
                let _ = respond_json(conn.stream(), &body, 400);
                break;
            }
            Ok(Some(req)) => {
                let reply =
                    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| handle(app, &req)))
                        .unwrap_or_else(|_| {
                            Reply::Json(
                                Json::Obj(vec![
                                    ("ok".into(), Json::Bool(false)),
                                    ("error".into(), Json::Str("internal error".into())),
                                ]),
                                500,
                            )
                        });
                match deliver(&mut conn, &req, &reply) {
                    Ok(code) => eprintln!(
                        "  \"{} {} HTTP/1.1\" {} -",
                        scrub(&req.method),
                        scrub(&req.target),
                        code
                    ),
                    Err(_) => break, // the client hung up mid-response
                }
            }
        }
    }
}
