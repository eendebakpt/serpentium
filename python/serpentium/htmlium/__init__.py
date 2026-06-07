"""htmlium -- a Rust accelerator for ``html.escape`` and ``html.unescape``.

This subpackage is the out-of-tree counterpart to CPython issue #151024 / PR #151025,
where a C accelerator for the same two functions was proposed and declined by several
core developers on maintenance-cost / timing grounds.  Instead of patching CPython,
``htmlium`` reimplements both functions in Rust and stays **bit-for-bit identical** to
the pure-Python stdlib by deriving its named/numeric character-reference tables from
CPython's own (see ``tools/generate_html_entities.py``).

Usage::

    import html
    import serpentium.htmlium as htmlium

    htmlium.install()                 # patch the stdlib ``html`` module in place
    html.unescape("Tom &amp; Jerry")  # now served by the Rust backend

    # or use the functions directly, without install()
    htmlium.escape('a <b> "c"')       # 'a &lt;b&gt; &quot;c&quot;'

How ``install()`` works
-----------------------
``html.escape`` and ``html.unescape`` are plain Python functions.  Rather than
*rebinding* the module attributes -- which would miss any reference taken before the
patch, notably ``html.parser``'s own ``unescape`` (captured with
``from html import unescape`` when ``html.parser`` is imported) -- :func:`install`
overwrites the **vectorcall slot of the function objects in place** (the same
technique :mod:`copium` and :mod:`floatium` use).  Because the object identity never
changes, every reference to it -- ``html.unescape``, ``html.parser.unescape``, and any
``from html import unescape`` anywhere -- dispatches to native code, and
:func:`uninstall` is a single pointer restore.  See ``INTERNALS.md`` for the details.

The fast path only handles the overwhelmingly common positional calls
(``escape(s)`` / ``escape(s, quote)`` / ``unescape(s)`` with a real ``str``); any
unusual call -- keyword arguments, a non-``str`` argument -- falls back to the saved
original implementation, so behaviour (including error messages) is unchanged.
"""

from __future__ import annotations

__all__ = ["HAVE_RUST_BACKEND", "escape", "install", "unescape", "uninstall"]

try:
    from serpentium._native import escape, unescape
    from serpentium._native import install as _c_install
    from serpentium._native import uninstall as _c_uninstall

    HAVE_RUST_BACKEND = True
except ImportError:  # pragma: no cover - exercised only on pure-Python installs
    escape = None  # ty: ignore[invalid-assignment]
    unescape = None  # ty: ignore[invalid-assignment]
    HAVE_RUST_BACKEND = False

_installed = False
# Set only when the attribute-rebind fallback is used (non-PyFunction target).
_rebind = None


def install():
    """Patch the stdlib ``html`` module in place to use the htmlium backend.

    Prefers in-place vectorcall patching of ``html.escape`` / ``html.unescape``, which
    also accelerates ``html.parser`` for free (it shares the ``unescape`` object). Falls
    back to attribute rebinding -- including ``html.parser.unescape`` explicitly -- if a
    target is not a plain Python function. Idempotent; undone by :func:`uninstall`.
    """
    global _installed, _rebind
    if _installed or not HAVE_RUST_BACKEND:
        return

    import html

    if not _c_install(html.escape, html.unescape):
        # Fallback (non-PyFunction target): rebind names. html.parser captured its own
        # `unescape` via `from html import unescape`, so patch that too. Dynamic setattr
        # (variable attr name) keeps both ruff B010 and ty quiet about monkeypatching.
        import html.parser as _hp

        patches = (
            (html, "escape", escape),
            (html, "unescape", unescape),
            (_hp, "unescape", unescape),
        )
        _rebind = [(mod, name, getattr(mod, name, None)) for mod, name, _fn in patches]
        for mod, name, fn in patches:
            setattr(mod, name, fn)
    _installed = True


def uninstall():
    """Restore the stdlib ``html`` module to its original implementation."""
    global _installed, _rebind
    if not _installed:
        return

    import html

    if _rebind is None:
        _c_uninstall(html.escape, html.unescape)
    else:
        for mod, name, orig in _rebind:
            if orig is not None:
                setattr(mod, name, orig)
        _rebind = None
    _installed = False
