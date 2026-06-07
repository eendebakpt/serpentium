"""Tests for the startup autopatch decision logic and the ``_run`` entry point."""

import random

import pytest
import serpentium
from serpentium import _autopatch


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("OFF", False),
        ("garbage", None),
    ],
)
def test_env_override_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("SERPENTIUM_AUTOPATCH", value)
    assert _autopatch._env_override() is expected


def test_unset_env_is_none(monkeypatch):
    monkeypatch.delenv("SERPENTIUM_AUTOPATCH", raising=False)
    assert _autopatch._env_override() is None


def test_env_overrides_marker(monkeypatch):
    # Even if a marker would disable, an explicit env=1 forces it on (and vice versa).
    monkeypatch.setattr(_autopatch, "_marker_present", lambda: True)
    monkeypatch.setenv("SERPENTIUM_AUTOPATCH", "1")
    assert _autopatch._should_autopatch() is True
    monkeypatch.setenv("SERPENTIUM_AUTOPATCH", "0")
    assert _autopatch._should_autopatch() is False


def test_run_respects_opt_out(monkeypatch):
    monkeypatch.setenv("SERPENTIUM_AUTOPATCH", "0")
    serpentium.uninstall()
    original = random.Random._randbelow
    _autopatch._run()
    try:
        assert random.Random._randbelow is original  # nothing was patched
    finally:
        serpentium.uninstall()


def test_run_installs_by_default(monkeypatch):
    monkeypatch.setenv("SERPENTIUM_AUTOPATCH", "1")
    serpentium.uninstall()
    original = random.Random._randbelow
    _autopatch._run()
    try:
        assert random.Random._randbelow is not original  # accelerators engaged
        assert random.Random._randbelow.__module__ == "serpentium.randium"
    finally:
        serpentium.uninstall()
