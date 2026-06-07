//! htmlium -- a Rust accelerator for `html.escape` and `html.unescape`.
//!
//! This is the out-of-tree counterpart to CPython issue #151024 / PR #151025, where
//! a C accelerator for the same two functions was proposed and declined by several
//! core developers on maintenance-cost / timing grounds (the `html.parser` rewrite
//! and a possible unified `xml.escape` were both cited as reasons it was premature).
//! Rather than patch CPython, htmlium reimplements both functions in Rust and stays
//! bit-for-bit identical to the pure-Python stdlib by deriving its entity tables from
//! CPython's own (`tools/generate_html_entities.py` -> `html_entities.rs`).
//!
//! Two faces share one core:
//!   * `escape_core` / `unescape_core` -- pure Rust, unit-tested, no Python.
//!   * `escape` / `unescape`           -- ergonomic `#[pyfunction]`s for the standalone
//!                                        `htmlium.escape(...)` API and the parity tests.
//!   * `*_vectorcall` + `install`/`uninstall` -- the in-place fast path: they overwrite
//!     the stdlib function objects' vectorcall slot (the same technique copium/floatium
//!     use) so every reference -- including the `from html import unescape` that
//!     `html.parser` captured at import -- dispatches to native code with no name rebind.

use std::borrow::Cow;
use std::os::raw::c_char;
use std::sync::atomic::{AtomicUsize, Ordering};

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::PyString;

use crate::html_entities::{ENTITIES, INVALID_CHARREFS, INVALID_CODEPOINTS};

// ---------------------------------------------------------------------------
// Table lookups (binary search over the generated, sorted arrays)
// ---------------------------------------------------------------------------

/// Exact named-reference lookup, e.g. `lookup_entity("amp;") == Some("&")`.
fn lookup_entity(name: &str) -> Option<&'static str> {
    ENTITIES
        .binary_search_by(|&(k, _)| k.cmp(name))
        .ok()
        .map(|i| ENTITIES[i].1)
}

/// Numeric refs that remap to a specific replacement char (`_invalid_charrefs`).
fn invalid_charref(num: u32) -> Option<&'static str> {
    INVALID_CHARREFS
        .binary_search_by(|&(k, _)| k.cmp(&num))
        .ok()
        .map(|i| INVALID_CHARREFS[i].1)
}

/// Numeric refs that resolve to the empty string (`_invalid_codepoints`).
fn is_invalid_codepoint(num: u32) -> bool {
    INVALID_CODEPOINTS.binary_search(&num).is_ok()
}

// ---------------------------------------------------------------------------
// escape -- mirrors html.escape (& < > and, when quote, " ')
// ---------------------------------------------------------------------------

#[inline]
fn is_special(b: u8, quote: bool) -> bool {
    matches!(b, b'&' | b'<' | b'>') || (quote && matches!(b, b'"' | b'\''))
}

/// `escape(s, quote)` over a Rust string. Returns `Cow::Borrowed` (the input
/// unchanged) when nothing needs escaping -- the common case -- so the caller can
/// hand back the original Python `str` object without allocating.
///
/// All five special characters are single-byte ASCII, so we scan and rewrite at the
/// byte level: non-special bytes (including every byte of a multi-byte UTF-8 sequence)
/// are copied verbatim, which keeps the result valid UTF-8 by construction.
pub fn escape_core(s: &str, quote: bool) -> Cow<'_, str> {
    let bytes = s.as_bytes();
    let start = match bytes.iter().position(|&b| is_special(b, quote)) {
        None => return Cow::Borrowed(s),
        Some(i) => i,
    };
    let mut out: Vec<u8> = Vec::with_capacity(s.len() + 16);
    out.extend_from_slice(&bytes[..start]);
    for &b in &bytes[start..] {
        match b {
            b'&' => out.extend_from_slice(b"&amp;"),
            b'<' => out.extend_from_slice(b"&lt;"),
            b'>' => out.extend_from_slice(b"&gt;"),
            b'"' if quote => out.extend_from_slice(b"&quot;"),
            b'\'' if quote => out.extend_from_slice(b"&#x27;"),
            _ => out.push(b),
        }
    }
    // Safe: original bytes were valid UTF-8 and every inserted sequence is ASCII.
    Cow::Owned(unsafe { String::from_utf8_unchecked(out) })
}

// ---------------------------------------------------------------------------
// unescape -- mirrors html.unescape, including the HTML5 numeric/named rules
// ---------------------------------------------------------------------------

/// Resolve a numeric code point to its replacement, applying the WHATWG
/// numeric-character-reference rules exactly as CPython's `_replace_charref` does.
fn resolve_numeric(num: u32) -> Cow<'static, str> {
    if let Some(r) = invalid_charref(num) {
        Cow::Borrowed(r)
    } else if (0xD800..=0xDFFF).contains(&num) || num > 0x10FFFF {
        Cow::Borrowed("\u{fffd}")
    } else if is_invalid_codepoint(num) {
        Cow::Borrowed("")
    } else {
        // Not a surrogate and <= 0x10FFFF, so from_u32 is always Some.
        Cow::Owned(char::from_u32(num).unwrap().to_string())
    }
}

/// Try to parse one character reference starting at byte `i` (which must be `&`).
///
/// Returns `Some((consumed, replacement))` when the stdlib regex
/// `&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)` would match here, and
/// `None` when it would not (in which case the caller leaves the `&` literal and
/// advances one byte -- exactly what `re.sub` does on a non-match).
///
/// The stdlib `{1,32}` cap on the named body is intentionally omitted: every HTML5
/// entity name fits in 32 chars, and the longest-prefix resolution below checks all
/// prefixes regardless, so an over-long unmatched body resolves to the same literal
/// text either way. The differential fuzz test guards this equivalence.
fn parse_charref(s: &str, i: usize) -> Option<(usize, Cow<'_, str>)> {
    let bytes = s.as_bytes();
    let n = bytes.len();
    debug_assert_eq!(bytes[i], b'&');
    let after = i + 1;
    if after >= n {
        return None;
    }

    if bytes[after] == b'#' {
        // ---- numeric character reference ----
        let mut j = after + 1;
        let hex = j < n && (bytes[j] == b'x' || bytes[j] == b'X');
        if hex {
            j += 1;
        }
        let digits_start = j;
        let mut num: u64 = 0;
        while j < n {
            let v = if hex {
                match bytes[j] {
                    b'0'..=b'9' => (bytes[j] - b'0') as u64,
                    b'a'..=b'f' => (bytes[j] - b'a' + 10) as u64,
                    b'A'..=b'F' => (bytes[j] - b'A' + 10) as u64,
                    _ => break,
                }
            } else {
                match bytes[j] {
                    b'0'..=b'9' => (bytes[j] - b'0') as u64,
                    _ => break,
                }
            };
            // Stop accumulating once past the max code point; the value stays
            // > 0x10FFFF, which resolves to U+FFFD regardless of later digits.
            if num <= 0x10FFFF {
                num = num * if hex { 16 } else { 10 } + v;
            }
            j += 1;
        }
        if j == digits_start {
            return None; // `&#` / `&#x` with no (hex) digit: regex did not match.
        }
        if j < n && bytes[j] == b';' {
            j += 1; // optional single trailing ';'
        }
        let num32 = if num > 0x10FFFF { 0x0011_0000 } else { num as u32 };
        Some((j - i, resolve_numeric(num32)))
    } else {
        // ---- named character reference ----
        let mut j = after;
        while j < n
            && !matches!(
                bytes[j],
                b'\t' | b'\n' | 0x0c | b' ' | b'<' | b'&' | b'#' | b';'
            )
        {
            j += 1;
        }
        if j == after {
            return None; // first char is excluded: no named body, regex did not match.
        }
        if j < n && bytes[j] == b';' {
            j += 1; // optional single trailing ';'
        }
        let end = j;
        let group1 = &s[after..end]; // body + optional ';'

        if let Some(val) = lookup_entity(group1) {
            return Some((end - i, Cow::Borrowed(val)));
        }
        // Longest-prefix fallback: x from len-1 down to 2 code points (stdlib's
        // `for x in range(len(s)-1, 1, -1)`), returning `_html5[s[:x]] + s[x:]`.
        let boundaries: Vec<usize> = group1
            .char_indices()
            .map(|(b, _)| b)
            .chain(std::iter::once(group1.len()))
            .collect();
        let nchars = boundaries.len() - 1;
        let mut x = nchars.saturating_sub(1);
        while x >= 2 {
            let prefix = &group1[..boundaries[x]];
            if let Some(val) = lookup_entity(prefix) {
                let tail = &group1[boundaries[x]..];
                return Some((end - i, Cow::Owned(format!("{val}{tail}"))));
            }
            x -= 1;
        }
        // No match: reproduce '&' + group1 verbatim (stdlib's `return '&' + s`).
        Some((end - i, Cow::Borrowed(&s[i..end])))
    }
}

/// `unescape(s)` over a Rust string. Returns `Cow::Borrowed` unchanged when there is
/// no `&` at all (mirrors stdlib's `if '&' not in s: return s`); otherwise a single
/// pass bulk-copies the gaps between references and substitutes each resolved ref.
pub fn unescape_core(s: &str) -> Cow<'_, str> {
    let bytes = s.as_bytes();
    // memchr (SIMD) to find each `&`; std's byte search is a scalar loop, which
    // loses to CPython's memchr-backed `'&' in s` on long entity-free strings.
    let Some(first) = memchr::memchr(b'&', bytes) else {
        return Cow::Borrowed(s);
    };
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut copied = 0usize;
    let mut i = first;
    loop {
        // bytes[i] == b'&' here.
        if let Some((consumed, repl)) = parse_charref(s, i) {
            out.extend_from_slice(&bytes[copied..i]);
            out.extend_from_slice(repl.as_bytes());
            i += consumed;
            copied = i;
        } else {
            i += 1;
        }
        match memchr::memchr(b'&', &bytes[i..]) {
            Some(off) => i += off,
            None => break,
        }
    }
    out.extend_from_slice(&bytes[copied..]);
    // Safe: gaps are copied from valid UTF-8 and every replacement is valid UTF-8.
    Cow::Owned(unsafe { String::from_utf8_unchecked(out) })
}

// ---------------------------------------------------------------------------
// Standalone #[pyfunction]s: htmlium.escape / htmlium.unescape, and parity tests.
// Both return the *same* str object when nothing changed (matching the no-copy
// fast path) and a fresh str otherwise.
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(signature = (s, quote=true))]
fn escape<'py>(s: &Bound<'py, PyString>, quote: bool) -> PyResult<Bound<'py, PyString>> {
    let st = s.to_str()?;
    Ok(match escape_core(st, quote) {
        Cow::Borrowed(_) => s.clone(),
        Cow::Owned(o) => PyString::new(s.py(), &o),
    })
}

#[pyfunction]
fn unescape<'py>(s: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyString>> {
    let st = s.to_str()?;
    Ok(match unescape_core(st) {
        Cow::Borrowed(_) => s.clone(),
        Cow::Owned(o) => PyString::new(s.py(), &o),
    })
}

// ---------------------------------------------------------------------------
// In-place vectorcall patching (the copium/floatium technique).
//
// We overwrite the stdlib `html.escape` / `html.unescape` function objects'
// `vectorcall` slot so all references to them -- including `html.parser.unescape`,
// which `html.parser` bound with `from html import unescape` at import time --
// dispatch straight to native code. The original slot pointers are saved so
// `uninstall` restores the exact pre-patch behaviour. Any unusual call (keyword
// args, wrong arity, a non-str argument) falls back to the saved original, so the
// drop-in is total: weird inputs get the stdlib's exact result and error messages.
// ---------------------------------------------------------------------------

use crate::set_function_vectorcall;

static SAVED_ESCAPE: AtomicUsize = AtomicUsize::new(0);
static SAVED_UNESCAPE: AtomicUsize = AtomicUsize::new(0);

/// Borrow a `&str` view of a Python `str`'s cached UTF-8 buffer. Returns `None`
/// (with the exception left set) only if the conversion fails, which never happens
/// for a real `str`. The buffer is guaranteed valid UTF-8, so validation is skipped.
unsafe fn borrow_utf8<'a>(obj: *mut ffi::PyObject) -> Option<&'a str> {
    let mut size: ffi::Py_ssize_t = 0;
    let ptr = ffi::PyUnicode_AsUTF8AndSize(obj, &mut size);
    if ptr.is_null() {
        return None;
    }
    Some(std::str::from_utf8_unchecked(std::slice::from_raw_parts(
        ptr as *const u8,
        size as usize,
    )))
}

unsafe fn new_pystr(s: &str) -> *mut ffi::PyObject {
    ffi::PyUnicode_FromStringAndSize(s.as_ptr() as *const c_char, s.len() as ffi::Py_ssize_t)
}

unsafe extern "C" fn escape_vectorcall(
    callable: *mut ffi::PyObject,
    args: *const *mut ffi::PyObject,
    nargsf: usize,
    kwnames: *mut ffi::PyObject,
) -> *mut ffi::PyObject {
    let nargs = ffi::PyVectorcall_NARGS(nargsf);
    if kwnames.is_null() && (nargs == 1 || nargs == 2) {
        let s_obj = *args;
        if ffi::PyUnicode_Check(s_obj) != 0 {
            if let Some(st) = borrow_utf8(s_obj) {
                let quote = if nargs == 2 {
                    let t = ffi::PyObject_IsTrue(*args.add(1));
                    if t < 0 {
                        return std::ptr::null_mut();
                    }
                    t != 0
                } else {
                    true
                };
                return match escape_core(st, quote) {
                    Cow::Borrowed(_) => {
                        ffi::Py_INCREF(s_obj);
                        s_obj
                    }
                    Cow::Owned(o) => new_pystr(&o),
                };
            }
            return std::ptr::null_mut();
        }
    }
    let orig: ffi::vectorcallfunc = std::mem::transmute(SAVED_ESCAPE.load(Ordering::Relaxed));
    orig(callable, args, nargsf, kwnames)
}

unsafe extern "C" fn unescape_vectorcall(
    callable: *mut ffi::PyObject,
    args: *const *mut ffi::PyObject,
    nargsf: usize,
    kwnames: *mut ffi::PyObject,
) -> *mut ffi::PyObject {
    let nargs = ffi::PyVectorcall_NARGS(nargsf);
    if kwnames.is_null() && nargs == 1 {
        let s_obj = *args;
        if ffi::PyUnicode_Check(s_obj) != 0 {
            if let Some(st) = borrow_utf8(s_obj) {
                return match unescape_core(st) {
                    Cow::Borrowed(_) => {
                        ffi::Py_INCREF(s_obj);
                        s_obj
                    }
                    Cow::Owned(o) => new_pystr(&o),
                };
            }
            return std::ptr::null_mut();
        }
    }
    let orig: ffi::vectorcallfunc = std::mem::transmute(SAVED_UNESCAPE.load(Ordering::Relaxed));
    orig(callable, args, nargsf, kwnames)
}

/// Patch `html.escape` / `html.unescape` in place. Returns `False` (a no-op) if
/// either target is not a plain Python function object exposing vectorcall, so the
/// Python wrapper can fall back to attribute rebinding.
#[pyfunction]
fn install(escape_fn: &Bound<'_, PyAny>, unescape_fn: &Bound<'_, PyAny>) -> bool {
    unsafe {
        let e = escape_fn.as_ptr();
        let u = unescape_fn.as_ptr();
        if ffi::PyFunction_Check(e) == 0 || ffi::PyFunction_Check(u) == 0 {
            return false;
        }
        let (Some(orig_e), Some(orig_u)) =
            (ffi::PyVectorcall_Function(e), ffi::PyVectorcall_Function(u))
        else {
            return false;
        };
        SAVED_ESCAPE.store(orig_e as usize, Ordering::Relaxed);
        SAVED_UNESCAPE.store(orig_u as usize, Ordering::Relaxed);
        set_function_vectorcall(e, Some(escape_vectorcall));
        set_function_vectorcall(u, Some(unescape_vectorcall));
    }
    true
}

/// Restore the original vectorcall slots saved by [`install`].
#[pyfunction]
fn uninstall(escape_fn: &Bound<'_, PyAny>, unescape_fn: &Bound<'_, PyAny>) {
    unsafe {
        let se = SAVED_ESCAPE.load(Ordering::Relaxed);
        let su = SAVED_UNESCAPE.load(Ordering::Relaxed);
        if se != 0 {
            set_function_vectorcall(escape_fn.as_ptr(), Some(std::mem::transmute(se)));
        }
        if su != 0 {
            set_function_vectorcall(unescape_fn.as_ptr(), Some(std::mem::transmute(su)));
        }
    }
}

/// Register the html functions on the `_native` module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(escape, m)?)?;
    m.add_function(wrap_pyfunction!(unescape, m)?)?;
    m.add_function(wrap_pyfunction!(install, m)?)?;
    m.add_function(wrap_pyfunction!(uninstall, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escape_basics() {
        assert_eq!(escape_core("a&b<c>d", true), "a&amp;b&lt;c&gt;d");
        assert_eq!(escape_core("\"'", true), "&quot;&#x27;");
        assert_eq!(escape_core("\"'", false), "\"'");
        assert!(matches!(escape_core("plain", true), Cow::Borrowed(_)));
    }

    #[test]
    fn unescape_named_and_numeric() {
        assert_eq!(unescape_core("&amp;"), "&");
        assert_eq!(unescape_core("&#62;"), ">");
        assert_eq!(unescape_core("&#x3e;"), ">");
        assert_eq!(unescape_core("caf&eacute;"), "caf\u{e9}");
        assert!(matches!(unescape_core("no entities"), Cow::Borrowed(_)));
    }

    #[test]
    fn unescape_edge_cases() {
        // Legacy entity without ';' followed by extra text -> longest-prefix rule.
        assert_eq!(unescape_core("&notit;"), "\u{ac}it;");
        assert_eq!(unescape_core("&notin;"), "\u{2209}");
        // Invalid / out-of-range numerics.
        assert_eq!(unescape_core("&#0;"), "\u{fffd}");
        assert_eq!(unescape_core("&#x110000;"), "\u{fffd}");
        assert_eq!(unescape_core("&#1;"), ""); // invalid codepoint -> empty
        // Non-matches left literal.
        assert_eq!(unescape_core("&#;"), "&#;");
        assert_eq!(unescape_core("&#x;"), "&#x;");
        assert_eq!(unescape_core("a & b"), "a & b");
        assert_eq!(unescape_core("&unknown;"), "&unknown;");
    }
}
