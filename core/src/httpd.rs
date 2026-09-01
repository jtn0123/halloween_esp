//! The studio server's transport half — studio_http.py, on a bare socket.
//!
//! Same seam as the Python split: none of this knows a route, a track or a
//! scene. It reads requests off a TcpStream (keep-alive, bounded bodies),
//! and writes the reply shapes the routes hand back — JSON, a validated
//! page, a Range-served file, or a relayed castle answer. Differences from
//! the Python on purpose: no gzip (content negotiation is optional, and a
//! DEFLATE implementation buys nothing on a loopback link) and no
//! content-hash ETag fallback (every HTML route supplies its own).
//!
//! Two files hold it, since the one file reached the repo's 500-line cap
//! and the tests it had no room for are the ones a parsing bug shows up
//! in first: [`http_parse`](crate::http_parse) reads requests,
//! [`http_resp`](crate::http_resp) writes replies. This module is the seam
//! they are reached through, so `httpd::Request` still means what it did.

pub use crate::http_parse::{parse_multipart, query_pairs, scrub, Conn, Request, MAX_BODY};
pub use crate::http_resp::{deliver, etag_matches, reason, respond, respond_json, Reply, CSP};
