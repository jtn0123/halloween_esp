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

use castle_core::httpd::{Conn, Reply, deliver, respond_json, scrub};
use castle_core::jsonio::Json;
use castle_core::studio::{Action, App, pending, repo_root};
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
    // Every rebuild, import and generator run is a child of this
    // interpreter. Asking it one question now beats watching each of them
    // fail later with a stack trace about numpy.
    if let Some(why) = castle_core::studio_scenes::check_py(&app.root) {
        eprintln!("studio: {why}");
        std::process::exit(1);
    }
    let _ = std::fs::create_dir(&app.tracks);
    let listener = match bind_retry(host, port) {
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
                if conn.close {
                    // The reply left the connection unusable — a body that
                    // came up short of its own Content-Length.
                    break;
                }
                match pending() {
                    Action::None => {}
                    Action::Stop => std::process::exit(0),
                    Action::Restart => restart_self(),
                }
            }
        }
    }
}

/// A restarted image races its own predecessor: the dying connections'
/// server-side sockets keep the port "in use" for a few dozen ms after
/// the exec (macOS SO_REUSEADDR only forgives TIME_WAIT), so a restart —
/// and only a restart, marked by the env var the exec set — retries the
/// bind. A port someone else really holds still fails on the first try.
fn bind_retry(host: &str, port: u16) -> std::io::Result<TcpListener> {
    let restarted = std::env::var_os("CASTLE_STUDIO_RESTART").is_some();
    // Unsafe since edition 2024, and the audit it asks for passes here: this
    // runs from serve() before the accept loop exists, so no other thread can
    // be reading the environment while it is written. The var is cleared so a
    // child the studio spawns does not inherit "you are a restart".
    unsafe { std::env::remove_var("CASTLE_STUDIO_RESTART") };
    let tries = if restarted { 100 } else { 1 };
    let mut last = None;
    for i in 0..tries {
        match bind_reuse(host, port) {
            Ok(l) => return Ok(l),
            Err(e) => last = Some(e),
        }
        if i + 1 < tries {
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
    }
    Err(last.unwrap_or_else(|| std::io::Error::other("bind never attempted")))
}

/// os.execv(sys.executable, sys.argv): the same process image again, PID
/// kept, after the response has actually gone out.
fn restart_self() -> ! {
    use std::os::unix::process::CommandExt;
    std::thread::sleep(std::time::Duration::from_millis(400));
    let exe = std::env::current_exe().unwrap_or_default();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let err = std::process::Command::new(exe)
        .args(args)
        .env("CASTLE_STUDIO_RESTART", "1")
        .exec();
    eprintln!("studio: restart failed: {err}");
    std::process::exit(1);
}

#[cfg(target_os = "macos")]
mod so {
    pub const SOL_SOCKET: i32 = 0xffff;
    pub const SO_REUSEADDR: i32 = 0x0004;
    #[repr(C)]
    pub struct SockaddrIn {
        pub sin_len: u8,
        pub sin_family: u8,
        pub sin_port: u16,
        pub sin_addr: u32,
        pub sin_zero: [u8; 8],
    }
    pub fn addr(family: u8, port: u16, ip: u32) -> SockaddrIn {
        SockaddrIn {
            sin_len: 16,
            sin_family: family,
            sin_port: port.to_be(),
            sin_addr: ip.to_be(),
            sin_zero: [0; 8],
        }
    }
}

#[cfg(not(target_os = "macos"))]
mod so {
    pub const SOL_SOCKET: i32 = 1;
    pub const SO_REUSEADDR: i32 = 2;
    #[repr(C)]
    pub struct SockaddrIn {
        pub sin_family: u16,
        pub sin_port: u16,
        pub sin_addr: u32,
        pub sin_zero: [u8; 8],
    }
    pub fn addr(family: u8, port: u16, ip: u32) -> SockaddrIn {
        SockaddrIn {
            sin_family: family as u16,
            sin_port: port.to_be(),
            sin_addr: ip.to_be(),
            sin_zero: [0; 8],
        }
    }
}

unsafe extern "C" {
    fn socket(domain: i32, ty: i32, protocol: i32) -> i32;
    // Variadic for real: fcntl(2) is `int fcntl(int, int, ...)`, and on
    // arm64 a variadic argument travels on the stack, not in x2. Declared
    // with a fixed third parameter the flag never arrives — FD_CLOEXEC is
    // set from whatever the stack happened to hold, so the restart's exec
    // inherits the old listener and the fresh image cannot rebind its own
    // port. It worked by luck until an unrelated edit moved the stack.
    fn fcntl(fd: i32, cmd: i32, ...) -> i32;
    fn setsockopt(fd: i32, level: i32, name: i32, value: *const i32, len: u32) -> i32;
    fn bind(fd: i32, addr: *const so::SockaddrIn, len: u32) -> i32;
    fn listen(fd: i32, backlog: i32) -> i32;
    fn close(fd: i32) -> i32;
}

/// TcpListener::bind with SO_REUSEADDR — what ThreadingHTTPServer's
/// allow_reuse_address does, without which a restart inside TIME_WAIT
/// cannot rebind its own port.
fn bind_reuse(host: &str, port: u16) -> std::io::Result<TcpListener> {
    use std::os::unix::io::FromRawFd;
    let ip: u32 = if host == "0.0.0.0" { 0 } else { 0x7f00_0001 };
    unsafe {
        let fd = socket(2, 1, 0); // AF_INET, SOCK_STREAM
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        // FD_CLOEXEC, or the restart's exec inherits the old listener
        // and the fresh image can never rebind its own port.
        fcntl(fd, 2, 1);
        let one: i32 = 1;
        if setsockopt(fd, so::SOL_SOCKET, so::SO_REUSEADDR, &one, 4) != 0 {
            let e = std::io::Error::last_os_error();
            close(fd);
            return Err(e);
        }
        let sa = so::addr(2, port, ip);
        if bind(fd, &sa, 16) != 0 || listen(fd, 128) != 0 {
            let e = std::io::Error::last_os_error();
            close(fd);
            return Err(e);
        }
        Ok(TcpListener::from_raw_fd(fd))
    }
}
