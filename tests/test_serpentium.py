"""Smoke tests for the top-level serpentium meta-package and install()/uninstall()."""

import random

import pytest
import serpentium
import serpentium.randium as randium


def test_status_reports_each_accelerator():
    st = serpentium.status()
    assert set(st) == {"randium", "datetimium", "htmlium", "floatium", "copium"}


@pytest.mark.skipif(not randium.HAVE_RUST_BACKEND, reason="randium Rust backend not compiled")
def test_install_patches_random_module():
    original_randint = random.randint
    original_randbelow = random.Random._randbelow_with_getrandbits
    # An instance created BEFORE install() must still get accelerated, because
    # install() patches the stdlib class in place rather than swapping it.
    pre_existing = random.Random(0)
    try:
        report = serpentium.install(floatium=False, copium=False, randium=True)
        assert report["randium"].startswith("on")
        # The stdlib class is patched in place (identity preserved), not swapped.
        assert random.Random is not randium.Random
        assert random.Random._randbelow_with_getrandbits is not original_randbelow
        # convenience functions are reproducible with a known seed
        random.seed(123)
        a = [random.randint(1, 100) for _ in range(20)]
        random.seed(123)
        b = [random.randint(1, 100) for _ in range(20)]
        assert a == b
        # the pre-existing instance now rides the Rust backend and matches a
        # fresh randium stream for the same seed
        pre_existing.seed(123)
        ref = randium.Random(123)
        assert [pre_existing.random() for _ in range(10)] == [ref.random() for _ in range(10)]
    finally:
        serpentium.uninstall()
    assert random.randint is original_randint
    assert random.Random._randbelow_with_getrandbits is original_randbelow


def test_copium_patch_enable_after_serpentium_install():
    """Regression: copium.patch.enable() must not raise after serpentium.install().

    serpentium used to do a raw ``copy.deepcopy = copium.deepcopy`` swap, bypassing
    copium's own state tracking.  A subsequent ``copium.patch.enable()`` then raised
    ``TypeError: copy.deepcopy is not a Python function`` because copium saw its own
    built-in already in place via an untracked path.
    """
    copium = pytest.importorskip("copium")
    patch = getattr(copium, "patch", None)
    if patch is None or not callable(getattr(patch, "enable", None)):
        pytest.skip("copium.patch.enable not available in this copium version")

    serpentium.install(floatium=False, randium=False, datetimium=False, htmlium=False)
    try:
        # Must not raise TypeError.
        copium.patch.enable()
        assert copium.patch.enabled()
    finally:
        serpentium.uninstall()


@pytest.mark.skipif(not randium.HAVE_RUST_BACKEND, reason="randium Rust backend not compiled")
def test_uninstall_is_idempotent():
    serpentium.uninstall()  # should be a no-op when nothing is installed
    serpentium.install(floatium=False, copium=False)
    serpentium.uninstall()
    serpentium.uninstall()
