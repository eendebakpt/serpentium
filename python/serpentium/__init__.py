"""serpentium — drop-in performance accelerators for CPython, bundled.

``serpentium`` is a meta-package that wires together several independent, drop-in
CPython accelerators behind a single :func:`install` call:

============  =======================================  ==================
Accelerator   Speeds up                                Provenance
============  =======================================  ==================
floatium      ``repr``/``str``/``float`` <-> string    eendebakpt
copium        ``copy.deepcopy``                        Bobronium
randium       ``random`` (Mersenne Twister, in Rust)   ships in serpentium
datetimium    ``datetime.strptime`` (fast path)        ships in serpentium
htmlium       ``html.escape`` / ``html.unescape``      ships in serpentium
============  =======================================  ==================

Typical use::

    import serpentium
    serpentium.install()        # turn everything on
    ...
    serpentium.uninstall()      # restore the stdlib

You can also reach for the pieces individually, e.g. ``serpentium.randium.Random``.
"""

from __future__ import annotations

from . import datetimium, htmlium, randium

__all__ = [
    "__version__",
    "datetimium",
    "htmlium",
    "install",
    "randium",
    "status",
    "uninstall",
]

try:  # the single source of truth is the version in pyproject.toml
    from importlib.metadata import version as _version

    __version__ = _version("serpentium")
except Exception:  # pragma: no cover - source tree without an installed dist
    __version__ = "0.0.0+unknown"


def _apply_floatium():
    """Enable floatium's fast float<->string conversions, if installed."""
    try:
        import floatium  # ty: ignore[unresolved-import]
    except ImportError:
        return False, "not installed"
    # floatium exposes an explicit installer in recent versions; older builds
    # patch on import. Be tolerant of both.
    for hook in ("install", "patch", "enable"):
        fn = getattr(floatium, hook, None)
        if callable(fn):
            fn()
            return True, f"floatium.{hook}()"
    return True, "active on import"


def _remove_floatium():
    try:
        import floatium  # ty: ignore[unresolved-import]
    except ImportError:
        return
    for hook in ("uninstall", "unpatch", "disable"):
        fn = getattr(floatium, hook, None)
        if callable(fn):
            fn()
            return


def _apply_copium():
    """Route ``copy.deepcopy`` through copium, if installed."""
    try:
        import copium  # ty: ignore[unresolved-import]
    except ImportError:
        return False, "not installed"
    # copium exposes a submodule ``copium.patch`` with ``enable``/``disable``;
    # check that before the top-level name loop so we never do a raw
    # ``copy.deepcopy`` swap that bypasses copium's own state tracking.
    _patch_ns = getattr(copium, "patch", None)
    if _patch_ns is not None:
        _enable_fn = getattr(_patch_ns, "enable", None)
        if callable(_enable_fn):
            _enable_fn()
            return True, "copium.patch.enable()"
    for hook in ("install", "patch", "enable"):
        fn = getattr(copium, hook, None)
        if callable(fn):
            fn()
            return True, f"copium.{hook}()"
    # Fall back to a manual swap if copium only exposes ``deepcopy``.
    deepcopy = getattr(copium, "deepcopy", None)
    if callable(deepcopy):
        import copy

        global _saved_deepcopy
        _saved_deepcopy = copy.deepcopy
        copy.deepcopy = deepcopy
        return True, "copy.deepcopy = copium.deepcopy"
    return False, "no usable entry point"


_saved_deepcopy = None


def _remove_copium():
    try:
        import copium  # ty: ignore[unresolved-import]
    except ImportError:
        return
    _patch_ns = getattr(copium, "patch", None)
    if _patch_ns is not None:
        _disable_fn = getattr(_patch_ns, "disable", None)
        if callable(_disable_fn):
            _disable_fn()
            return
    for hook in ("uninstall", "unpatch", "disable"):
        fn = getattr(copium, hook, None)
        if callable(fn):
            fn()
            return
    global _saved_deepcopy
    if _saved_deepcopy is not None:
        import copy

        copy.deepcopy = _saved_deepcopy
        _saved_deepcopy = None


def install(*, floatium=True, copium=True, randium=True, datetimium=True, htmlium=True):
    """Enable the bundled accelerators.

    Each accelerator can be toggled individually. Missing optional packages
    (floatium, copium) are skipped with a note rather than raising; randium, htmlium
    and datetimium ship inside serpentium (randium and htmlium need the compiled Rust
    backend; datetimium is pure Python and always available).

    Returns a dict mapping accelerator name -> status string.
    """
    report = {}
    if floatium:
        ok, msg = _apply_floatium()
        report["floatium"] = ("on: " if ok else "skipped: ") + msg
    if copium:
        ok, msg = _apply_copium()
        report["copium"] = ("on: " if ok else "skipped: ") + msg
    if randium:
        from . import randium as _randium_mod

        _randium_mod.install()
        report["randium"] = "on: rust backend"
    if datetimium:
        from . import datetimium as _datetimium_mod

        _datetimium_mod.install()
        report["datetimium"] = "on: fast datetime.strptime"
    if htmlium:
        from . import htmlium as _htmlium_mod

        if _htmlium_mod.HAVE_RUST_BACKEND:
            _htmlium_mod.install()
            report["htmlium"] = "on: rust html.escape/unescape"
        else:
            report["htmlium"] = "skipped: rust backend not compiled"
    return report


def uninstall():
    """Restore every patched stdlib component to its original implementation."""
    from . import datetimium as _datetimium_mod
    from . import htmlium as _htmlium_mod
    from . import randium as _randium_mod

    _htmlium_mod.uninstall()
    _datetimium_mod.uninstall()
    _randium_mod.uninstall()
    _remove_copium()
    _remove_floatium()


def status():
    """Return a dict describing which accelerators are importable/active."""
    out = {
        "randium": "rust" if randium.HAVE_RUST_BACKEND else "unavailable",
        "datetimium": "python",
        "htmlium": "rust" if htmlium.HAVE_RUST_BACKEND else "unavailable",
    }
    for name in ("floatium", "copium"):
        try:
            __import__(name)
            out[name] = "installed"
        except ImportError:
            out[name] = "not installed"
    return out
