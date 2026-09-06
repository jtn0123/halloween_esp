//! The line reader under the YAML subset — comments off, indentation
//! measured, `key:` found. Split from [`crate::yaml_parse`] at the repo's
//! 500-line cap along the obvious seam: this is lexing, that is structure.

use crate::yaml::YamlError;

#[derive(Debug)]
pub(crate) struct Line {
    pub(crate) no: usize,
    pub(crate) indent: usize,
    pub(crate) text: String,
    pub(crate) blank: bool,
}

/// Everything up to an unquoted `#` that follows whitespace or starts the
/// line — the same rule PyYAML applies to a plain scalar.
fn strip_comment(s: &str) -> &str {
    let b = s.as_bytes();
    let mut quote: Option<u8> = None;
    let mut prev_ws = true;
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        match quote {
            Some(q) => {
                if q == b'"' && c == b'\\' {
                    i += 2;
                    prev_ws = false;
                    continue;
                }
                if c == q {
                    quote = None;
                }
            }
            None => {
                if c == b'"' || c == b'\'' {
                    quote = Some(c);
                } else if c == b'#' && prev_ws {
                    return &s[..i];
                }
            }
        }
        prev_ws = c == b' ' || c == b'\t';
        i += 1;
    }
    s
}

pub(crate) fn split_lines(text: &str) -> Result<Vec<Line>, YamlError> {
    let mut out = Vec::new();
    for (i, raw) in text.lines().enumerate() {
        let no = i + 1;
        let body = strip_comment(raw);
        let indent = body.len() - body.trim_start_matches(' ').len();
        if body.trim().is_empty() {
            out.push(Line {
                no,
                indent: 0,
                text: String::new(),
                blank: true,
            });
            continue;
        }
        let ws: usize = body
            .chars()
            .take_while(|c| *c == ' ' || *c == '\t')
            .map(char::len_utf8)
            .sum();
        if body[..ws].contains('\t') {
            return Err(YamlError {
                line: no,
                msg: "tabs cannot be used to indent".into(),
            });
        }
        out.push(Line {
            no,
            indent,
            text: body.trim().to_string(),
            blank: false,
        });
    }
    Ok(out)
}

/// The `:` that separates a block mapping's key from its value — the first
/// one outside quotes that is followed by a space or ends the line.
pub(crate) fn key_colon(s: &str) -> Option<usize> {
    let b = s.as_bytes();
    let mut quote: Option<u8> = None;
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        match quote {
            Some(q) => {
                if q == b'"' && c == b'\\' {
                    i += 2;
                    continue;
                }
                if c == q {
                    quote = None;
                }
            }
            None => {
                if c == b'"' || c == b'\'' {
                    quote = Some(c);
                } else if c == b'{' || c == b'[' {
                    return None; // a flow collection, not a key
                } else if c == b':' && (i + 1 == b.len() || b[i + 1] == b' ') {
                    return Some(i);
                }
            }
        }
        i += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `#` is a comment only at the start of a line or after whitespace,
    /// and never inside quotes — `note: "roll #3"` keeps its hash.
    #[test]
    fn a_hash_is_a_comment_only_where_yaml_says_it_is() {
        assert_eq!(strip_comment("id: trial   # the id"), "id: trial   ");
        assert_eq!(strip_comment("id: a#b"), "id: a#b");
        assert_eq!(strip_comment("# whole line"), "");
        assert_eq!(strip_comment("note: \"roll #3\""), "note: \"roll #3\"");
        assert_eq!(strip_comment("note: 'a # b' # gone"), "note: 'a # b' ");
        assert_eq!(
            strip_comment("note: \"a \\\" # b\""),
            "note: \"a \\\" # b\""
        );
        assert_eq!(strip_comment("no hash here"), "no hash here");
    }

    /// Blank lines survive as blanks (a block scalar needs them), and a
    /// tab where indentation belongs is refused rather than guessed at.
    #[test]
    fn lines_carry_their_number_indent_and_emptiness() {
        let ls = split_lines("a: 1\n\n  b: 2   # x\n   \n").expect("reads");
        assert_eq!(ls.len(), 4);
        assert_eq!(
            (ls[0].no, ls[0].indent, ls[0].text.as_str()),
            (1, 0, "a: 1")
        );
        assert!(ls[1].blank);
        assert_eq!(
            (ls[2].no, ls[2].indent, ls[2].text.as_str()),
            (3, 2, "b: 2")
        );
        assert!(ls[3].blank);
        let err = split_lines("a: 1\n\tb: 2\n").expect_err("tabs");
        assert_eq!(err.to_string(), "line 2: tabs cannot be used to indent");
    }

    /// The `:` that ends a key is the first one outside quotes that is
    /// followed by a space or ends the line — and a value that opens a
    /// flow collection has no key colon at all.
    #[test]
    fn the_key_colon_is_the_first_one_that_separates() {
        assert_eq!(key_colon("id: trial"), Some(2));
        assert_eq!(key_colon("cues:"), Some(4));
        assert_eq!(key_colon("note: a: b"), Some(4));
        assert_eq!(key_colon("\"a: b\": c"), Some(6));
        assert_eq!(key_colon("{towerL: candle}"), None);
        assert_eq!(key_colon("[1, 2]"), None);
        assert_eq!(key_colon("just text"), None);
        assert_eq!(key_colon("a:b"), None);
    }
}
