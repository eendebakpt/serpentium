"""Regression guard: CPython's own ``test_random`` must pass under serpentium.

This is the in-process, fast counterpart to ``tools/cpython_suite.py`` (which can
drive the *entire* CPython regression suite, optionally in parallel).  Here we run
the single most load-bearing stdlib module -- ``test.test_random`` -- with
serpentium installed, so any drift in the in-place ``random`` patch is caught by a
normal ``pytest`` run.
"""

import unittest

import pytest

randium = pytest.importorskip("serpentium.randium")

if not randium.HAVE_RUST_BACKEND:
    pytest.skip("randium Rust backend not compiled", allow_module_level=True)

# The CPython regression suite ships with most full installs but not all (e.g.
# slimmed-down distro packages). Skip cleanly if it isn't importable here.
test_random = pytest.importorskip("test.test_random")


@pytest.fixture
def serpentium_installed():
    import serpentium

    serpentium.install()
    try:
        yield
    finally:
        serpentium.uninstall()


def test_cpython_test_random_passes_under_serpentium(serpentium_installed):
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_random)
    result = unittest.TestResult()
    suite.run(result)

    # Surface concrete failures rather than a bare assert.
    problems = [
        f"{kind}: {test.id()}\n{tb}"
        for kind, entries in (("FAIL", result.failures), ("ERROR", result.errors))
        for test, tb in entries
    ]
    assert not problems, (
        f"{len(problems)} CPython test_random checks regressed under serpentium:\n\n"
        + "\n".join(problems)
    )
