"""Autopatch hook.

This module is imported by the ``site-packages/serpentium-autopatch.pth`` file at
interpreter startup, so installing serpentium turns the accelerators on for every
Python process with no ``import serpentium`` / ``serpentium.install()`` needed.

Default behaviour is to install; opt out with either:

  * env var: ``SERPENTIUM_AUTOPATCH=0`` (or ``false`` / ``no`` / ``off``)
  * CLI:     ``python -m serpentium disable`` -- writes a marker file
             ``serpentium-autopatch.disabled`` next to the ``.pth``

An explicitly-set env var wins over the marker file, so ad-hoc opt-out works in CI
even when the marker is absent.  Set ``SERPENTIUM_AUTOPATCH_DEBUG=1`` to print
install failures (otherwise they are swallowed so startup can never break).
"""

from __future__ import annotations

import os

_MARKER_NAME = "serpentium-autopatch.disabled"


def _env_override():
    """Parse SERPENTIUM_AUTOPATCH; return True/False, or None if unset/garbage."""
    v = os.environ.get("SERPENTIUM_AUTOPATCH")
    if v is None:
        return None
    s = v.strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None


def _marker_present():
    """True if the disable-marker sits next to the .pth in this install."""
    try:
        # __file__ is .../site-packages/serpentium/_autopatch.py; the marker lives
        # next to the .pth, i.e. in .../site-packages/.
        sp = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        return os.path.isfile(os.path.join(sp, _MARKER_NAME))
    except Exception:
        return False


def _should_autopatch():
    explicit = _env_override()
    if explicit is not None:
        return explicit
    return not _marker_present()


def _run():
    if not _should_autopatch():
        return

    # Import lazily so merely having the .pth on disk doesn't drag the compiled
    # backend into every Python process -- only into ones where autopatch fires.
    try:
        import serpentium
        from serpentium import htmlium, randium
    except ImportError:
        return

    try:
        # Gate the Rust-backed accelerators on the compiled backend so a source/
        # no-build checkout still auto-enables the pure-Python accelerators instead
        # of raising.
        serpentium.install(
            randium=randium.HAVE_RUST_BACKEND,
            htmlium=htmlium.HAVE_RUST_BACKEND,
        )
    except Exception:
        if os.environ.get("SERPENTIUM_AUTOPATCH_DEBUG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            import traceback

            traceback.print_exc()


_run()
