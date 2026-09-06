//! The lean rewrite — gen_previewer.lean, the one pass that turns the
//! portable desk page into the one the studio and the card actually serve.
//!
//! Split out of studio.rs on its own seam: everything here is string
//! surgery on the built page, and nothing here knows where the show lives
//! or which routes exist. `studio` re-exports both names, so callers still
//! say `studio::lean`.

pub const AUDIO_ROUTE: &str = "/studio/scene-audio/";

/// gen_previewer.lean — every inlined scene audio swapped for its URL:
/// `"(\w+)": ?"data:audio/mpeg;base64,…"` becomes `"<id>": "<route><id>"`.
pub fn lean(html: &str) -> String {
    const NEEDLE: &str = "data:audio/mpeg;base64,";
    let b = html.as_bytes();
    let mut out = String::with_capacity(html.len() / 4);
    let mut copied = 0usize;
    let mut from = 0usize;
    while let Some(rel) = html[from..].find(NEEDLE) {
        let pos = from + rel;
        from = pos + NEEDLE.len();
        if pos == 0 || b[pos - 1] != b'"' {
            continue;
        }
        // Walk back over `": ?"` to the key, which must be "\w+".
        let mut k = pos - 1;
        if k > 0 && b[k - 1] == b' ' {
            k -= 1;
        }
        if k == 0 || b[k - 1] != b':' {
            continue;
        }
        k -= 1;
        if k == 0 || b[k - 1] != b'"' {
            continue;
        }
        k -= 1;
        let mut id_start = k;
        while id_start > 0 && (b[id_start - 1].is_ascii_alphanumeric() || b[id_start - 1] == b'_') {
            id_start -= 1;
        }
        if id_start == k || id_start == 0 || b[id_start - 1] != b'"' {
            continue;
        }
        let id = &html[id_start..k];
        // Forward over the base64 body to the closing quote.
        let mut end = pos + NEEDLE.len();
        while end < b.len()
            && (b[end].is_ascii_alphanumeric() || matches!(b[end], b'+' | b'/' | b'='))
        {
            end += 1;
        }
        if end >= b.len() || b[end] != b'"' {
            continue;
        }
        out.push_str(&html[copied..id_start - 1]);
        out.push('"');
        out.push_str(id);
        out.push_str("\": \"");
        out.push_str(AUDIO_ROUTE);
        out.push_str(id);
        out.push('"');
        copied = end + 1;
        from = end + 1;
    }
    out.push_str(&html[copied..]);
    out
}
