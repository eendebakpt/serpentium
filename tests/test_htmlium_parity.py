"""htmlium parity tests: the Rust html.escape/unescape must be indistinguishable
from the pure-Python stdlib.

The accelerator is only acceptable if it is a *true* drop-in, so these tests compare
it against the unpatched stdlib across the entire HTML5 named-entity table, the full
numeric character-reference range (including the invalid / surrogate / out-of-range
rules), and a large random differential fuzz -- asserting identical results.  They
also exercise the in-place vectorcall install: that it patches the function objects
without changing their identity, accelerates ``html.parser.unescape`` for free, and
restores everything exactly on uninstall.
"""

from __future__ import annotations

import html
import html.parser
import random
from html.entities import html5

import pytest
import serpentium.htmlium as htmlium

pytestmark = pytest.mark.skipif(
    not htmlium.HAVE_RUST_BACKEND, reason="htmlium Rust backend not compiled"
)

# The unpatched stdlib references, captured at import time so the comparison is
# always against the genuine pure-Python implementation even if some other test
# installs serpentium globally.
_STD_ESCAPE = html.escape
_STD_UNESCAPE = html.unescape


# ---------------------------------------------------------------------------
# escape
# ---------------------------------------------------------------------------

ESCAPE_CASES = [
    "",
    "no special characters here",
    "a & b",
    "<tag>",
    "mix & < > \" '",
    "&amp; already escaped",
    "unicode café ☃ \U0001f600 stays",
    "ends with &",
    "\"'\"'",
]


@pytest.mark.parametrize("s", ESCAPE_CASES)
@pytest.mark.parametrize("quote", [True, False])
def test_escape_matches_stdlib(s, quote):
    assert htmlium.escape(s, quote) == _STD_ESCAPE(s, quote)


def test_escape_default_quote_is_true():
    assert htmlium.escape('"') == _STD_ESCAPE('"') == "&quot;"


def test_escape_returns_same_object_when_unchanged():
    # No-copy fast path: nothing to escape -> the original str object comes back.
    s = "perfectly ordinary text"
    assert htmlium.escape(s) is s


# ---------------------------------------------------------------------------
# unescape
# ---------------------------------------------------------------------------


def test_unescape_returns_same_object_when_no_ampersand():
    s = "no entities at all"
    assert htmlium.unescape(s) is s


def test_unescape_all_named_entities():
    """Every one of the 2231 HTML5 named references, with several surrounding contexts."""
    diffs = []
    for name in html5:
        for s in (f"&{name}", f"&{name};", f"x&{name}y", f"&{name}extra;", f"a &{name} b"):
            if htmlium.unescape(s) != _STD_UNESCAPE(s):
                diffs.append(s)
    assert not diffs, f"{len(diffs)} named-entity divergences, e.g. {diffs[:5]}"


@pytest.mark.parametrize(
    "num",
    [
        0,
        1,
        8,
        0x0B,
        0x0D,
        0x20,
        0x41,
        0x7F,
        0x80,
        0x9F,
        0xD7FF,
        0xD800,
        0xDFFF,
        0xE000,
        0xFFFD,
        0xFFFE,
        0xFFFF,
        0x10000,
        0xFDD0,
        0xFDEF,
        0x10FFFF,
        0x110000,
        0x7FFFFFFF,
    ],
)
def test_unescape_numeric_charrefs(num):
    forms = [f"&#{num};", f"&#{num}", f"&#x{num:x};", f"&#X{num:X}", f"a&#{num};b"]
    for s in forms:
        assert htmlium.unescape(s) == _STD_UNESCAPE(s), s


@pytest.mark.parametrize(
    "s",
    [
        "&notit;",  # longest-prefix legacy match -> '\xacit;'
        "&notin;",  # exact entity
        "&amp;&amp;",  # consecutive
        "&#;",  # bare numeric, no digits -> literal
        "&#x;",  # bare hex, no digits -> literal
        "&# 123;",  # space breaks numeric -> literal
        "a & b & c",  # lone ampersands
        "&unknownentity;",
        "&AMP",  # legacy, no semicolon
        "&#0000065;",  # leading zeros -> 'A'
        "&#x1F600;",  # astral plane emoji
        "plain &amp; <b>tag</b>",
    ],
)
def test_unescape_edge_cases(s):
    assert htmlium.unescape(s) == _STD_UNESCAPE(s)


# ---------------------------------------------------------------------------
# differential fuzz
# ---------------------------------------------------------------------------

# An alphabet weighted toward the structural characters of character references so
# the fuzz actually exercises the entity-resolution paths, not just bulk copying.
_FUZZ_ALPHABET = list(
    "&#;xX<>\"'/ \tabcdef AMP amp lt gt quot eacute notin notit 0123456789 zé\U0001f600"
)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_differential_fuzz(seed):
    rng = random.Random(seed)
    for _ in range(20_000):
        s = "".join(rng.choice(_FUZZ_ALPHABET) for _ in range(rng.randint(0, 48)))
        assert htmlium.unescape(s) == _STD_UNESCAPE(s), ("unescape", s)
        assert htmlium.escape(s) == _STD_ESCAPE(s), ("escape", s)
        assert htmlium.escape(s, False) == _STD_ESCAPE(s, False), ("escape/noquote", s)


# ---------------------------------------------------------------------------
# in-place install / uninstall mechanics
# ---------------------------------------------------------------------------


def test_install_patches_in_place_and_restores():
    escape_obj = html.escape
    unescape_obj = html.unescape
    # html.parser captured its own reference with `from html import unescape`.
    assert html.parser.unescape is unescape_obj

    htmlium.install()
    try:
        # Same object identity (vectorcall slot patched, not the name rebound).
        assert html.escape is escape_obj
        assert html.unescape is unescape_obj
        # html.parser is accelerated for free, since it shares the object.
        assert html.parser.unescape is unescape_obj
        # ...and now produces the accelerated result.
        assert html.unescape("Tom &amp; Jerry") == "Tom & Jerry"
        assert html.escape("a & b") == "a &amp; b"
        # Keyword / unusual calls fall back to the stdlib and stay correct
        # (quote only affects " and '; & < > are always escaped).
        assert html.escape('a"b', quote=False) == 'a"b'
        assert html.escape('a"b', quote=True) == "a&quot;b"
        assert html.escape(s="x<y") == "x&lt;y"
    finally:
        htmlium.install()  # idempotent
        htmlium.uninstall()

    # Restored: still the same objects, still correct, behaviour unchanged.
    assert html.escape is escape_obj
    assert html.unescape is unescape_obj
    assert html.unescape("Tom &amp; Jerry") == "Tom & Jerry"


def test_uninstall_without_install_is_noop():
    htmlium.uninstall()  # must not raise
