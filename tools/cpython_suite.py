#!/usr/bin/env python3
"""Run CPython's own regression test suite with serpentium installed.

serpentium monkeypatches stdlib modules (``random`` today, ``datetime.strptime``
next).  The load-bearing question for any such drop-in is: *does the standard
library's own test suite still pass when our accelerator is active?*  This script
answers that by running CPython's ``test`` package (the very suite that ships with
the running interpreter) under ``serpentium.install()``.

How it works
------------
``python -m test`` (regrtest) runs each test module, optionally across worker
subprocesses (``-j``).  To make serpentium active in every worker, we drop a
temporary ``usercustomize.py`` on ``PYTHONPATH`` that calls ``serpentium.install()``
at interpreter startup (gated by the ``SERPENTIUM_AUTOINSTALL`` env var), then
invoke regrtest as a subprocess.  This works identically for single-process and
``-j`` parallel runs.

Usage
-----
    python tools/cpython_suite.py                 # curated, serpentium-affected subset
    python tools/cpython_suite.py --all           # the ENTIRE CPython suite
    python tools/cpython_suite.py test_random test_datetime
    python tools/cpython_suite.py --no-install    # baseline: suite WITHOUT serpentium
    python tools/cpython_suite.py --all -- -j8 -v # forward args after -- to regrtest

Exit code is regrtest's, so this is CI-friendly.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile

# Stdlib test modules most directly exercised by what serpentium patches.
# (Modules that don't exist on a given version are skipped automatically.)
CURATED = [
    "test_random",  # the Mersenne-Twister / randint / _randbelow surface
    "test_datetime",  # datetime.strptime accelerator (once enabled)
    "test_strptime",  # _strptime internals
    "test_uuid",  # uuid4 leans on the random machinery
    "test_statistics",  # consumes random heavily
    "test_html",  # html.escape / html.unescape accelerator (htmlium)
    "test_htmlparser",  # HTMLParser, which calls the patched html.unescape
]

_USERCUSTOMIZE = """\
# Injected by serpentium tools/cpython_suite.py -- installs the accelerators at
# interpreter startup so they are active inside every regrtest worker.
import os
if os.environ.get("SERPENTIUM_AUTOINSTALL") == "1":
    try:
        import serpentium
        from serpentium import randium as _r
        # A subprocess spawned by a test may not be able to import the compiled
        # randium backend; install whatever is available rather than all-or-nothing.
        serpentium.install(randium=_r.HAVE_RUST_BACKEND)
    except Exception as exc:  # pragma: no cover - surfaced, never swallowed silently
        import sys
        print("[serpentium] startup install failed:", exc, file=sys.stderr)
"""


def _existing(modules):
    """Keep only test modules importable in this interpreter's ``test`` package."""
    keep = []
    for name in modules:
        if importlib.util.find_spec(f"test.{name}") is not None:
            keep.append(name)
        else:
            print(f"[cpython_suite] skipping {name} (not present in this stdlib)")
    return keep


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "tests", nargs="*", help="specific stdlib test modules (default: curated subset)"
    )
    parser.add_argument(
        "--all", action="store_true", help="run the entire CPython regression suite"
    )
    parser.add_argument(
        "--no-install", action="store_true", help="run WITHOUT serpentium (baseline for comparison)"
    )
    parser.add_argument(
        "regrtest_args", nargs="*", help="args after `--` are forwarded verbatim to regrtest"
    )
    # argparse can't cleanly split two nargs='*'; do it by hand around `--`.
    raw = list(sys.argv[1:] if argv is None else argv)
    forward = []
    if "--" in raw:
        i = raw.index("--")
        raw, forward = raw[:i], raw[i + 1 :]
    args = parser.parse_args(raw)

    if not args.no_install and importlib.util.find_spec("serpentium") is None:
        print(
            "error: serpentium is not importable; build it first "
            "(maturin develop) or pass --no-install",
            file=sys.stderr,
        )
        return 2

    if args.all:
        test_names = []  # regrtest runs everything when given no names
    else:
        test_names = _existing(args.tests or CURATED)
        if not test_names:
            print("error: none of the requested test modules exist here", file=sys.stderr)
            return 2

    env = dict(os.environ)
    tmpdir = None
    if not args.no_install:
        tmpdir = tempfile.mkdtemp(prefix="serpentium-suite-")
        with open(os.path.join(tmpdir, "usercustomize.py"), "w") as fh:
            fh.write(_USERCUSTOMIZE)
        env["SERPENTIUM_AUTOINSTALL"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(
            [tmpdir] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )

    cmd = [sys.executable, "-m", "test", *forward, *test_names]
    mode = "WITHOUT serpentium (baseline)" if args.no_install else "with serpentium installed"
    print(f"[cpython_suite] running {mode}:\n    {' '.join(cmd)}\n", flush=True)
    try:
        return subprocess.call(cmd, env=env)
    finally:
        if tmpdir:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
