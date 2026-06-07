"""datetimium parity tests: the fast strptime must be indistinguishable from stdlib.

The accelerator is only acceptable if it is *fully* backwards compatible, so these
tests compare it against the unpatched stdlib across valid data, malformed data,
and random noise -- asserting identical results AND identical exceptions.
"""

import random
from datetime import datetime as DT

import pytest
import serpentium.datetimium as dtm

SUPPORTED_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%d/%m/%Y",
    "%m/%d/%Y %H:%M",
    "%Y%m%d",
    "%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S.%f",
    "%S",
    "%M:%S",
    "%Y",
    "%Y %m %d",
    "  %Y-%m-%d  ",
    "100%%done %Y",
    "%Y-%m-%dT%H:%M:%S",  # case-insensitive 'T'
]

# Formats outside the accelerated subset -> must transparently defer to stdlib.
DEFERRED_FORMATS = [
    "%d-%b-%Y",  # %b is locale-dependent
    "%a %b %d %Y",  # weekday + month names
    "%j %Y",  # day-of-year
    "%y-%m-%d",  # 2-digit year (century pivot)
    "%Y-%W-%w",  # week number
    "%z %Y",  # tz offset
    "%G-%V-%u",  # ISO calendar
]


def _outcome_stdlib(s, f):
    try:
        return ("ok", DT.strptime(s, f))
    except Exception as e:
        return ("err", type(e), str(e))


def _outcome_fast(s, f):
    try:
        return ("ok", dtm.strptime(s, f))
    except Exception as e:
        return ("err", type(e), str(e))


@pytest.mark.parametrize("fmt", SUPPORTED_FORMATS + DEFERRED_FORMATS)
def test_valid_roundtrip_matches(fmt):
    rng = random.Random(hash(fmt) & 0xFFFF)
    for _ in range(300):
        try:
            sample = DT(
                rng.randint(1, 9999),
                rng.randint(1, 12),
                rng.randint(1, 28),
                rng.randint(0, 23),
                rng.randint(0, 59),
                rng.randint(0, 59),
                rng.randint(0, 999999),
            ).strftime(fmt)
        except ValueError:
            continue
        assert _outcome_fast(sample, fmt) == _outcome_stdlib(sample, fmt), (sample, fmt)


@pytest.mark.parametrize("fmt", SUPPORTED_FORMATS)
def test_malformed_and_noise_matches(fmt):
    rng = random.Random(0xC0FFEE ^ hash(fmt))
    noise = "0123456789-/:T. %abcz\t.+"
    for _ in range(4000):
        s = "".join(rng.choice(noise) for _ in range(rng.randint(0, 16)))
        assert _outcome_fast(s, fmt) == _outcome_stdlib(s, fmt), (s, fmt)


requires_hook = pytest.mark.skipif(
    not dtm.SUPPORTED, reason="datetimium strptime hook only exists on CPython 3.14+"
)


@requires_hook
def test_fast_path_engages_for_supported_formats(monkeypatch):
    """A supported format + valid input must NOT fall through to stdlib."""

    def _boom(*a, **k):
        raise AssertionError("unexpectedly fell back to stdlib")

    monkeypatch.setattr(dtm, "_fallback", _boom)
    assert dtm.strptime("2021-07-15 13:45:30", "%Y-%m-%d %H:%M:%S") == DT(2021, 7, 15, 13, 45, 30)
    assert dtm.strptime("20210715", "%Y%m%d") == DT(2021, 7, 15)


@requires_hook
def test_unsupported_format_defers(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(dtm, "_fallback", lambda *a, **k: sentinel)
    assert dtm.strptime("Mon Jul 15 2021", "%a %b %d %Y") is sentinel


@pytest.mark.parametrize(
    "s,fmt",
    [
        ("2021-02-30", "%Y-%m-%d"),  # invalid day -> ValueError
        ("2021-13-01", "%Y-%m-%d"),  # leftover/no-match
        ("2021-07-15x", "%Y-%m-%d"),  # unconverted data remains
        ("0000-01-01", "%Y-%m-%d"),  # year 0 -> ValueError
        ("2021-07", "%Y-%m-%d"),  # truncated
        ("99:99:99", "%H:%M:%S"),  # out of range
    ],
)
def test_error_cases_match_exactly(s, fmt):
    assert _outcome_fast(s, fmt) == _outcome_stdlib(s, fmt)


@requires_hook
def test_install_uninstall_patches_datetime_strptime():
    import _strptime

    original = _strptime._strptime_datetime_datetime
    import serpentium

    serpentium.install(floatium=False, copium=False, randium=False, datetimium=True)
    try:
        assert _strptime._strptime_datetime_datetime is not original
        # The C datetime.strptime now routes through the accelerator.
        assert DT.strptime("2021-07-15", "%Y-%m-%d") == DT(2021, 7, 15)
    finally:
        serpentium.uninstall()
    assert _strptime._strptime_datetime_datetime is original
