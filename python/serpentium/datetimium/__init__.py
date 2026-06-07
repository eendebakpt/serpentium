"""datetimium -- a fully-compatible fast path for ``datetime.datetime.strptime``.

``datetime.strptime`` is surprisingly slow (~4-5 us/call on CPython 3.14): the C
method defers to the pure-Python :mod:`_strptime`, which on *every* call rebuilds a
field dict from a regex match, runs a per-directive Python loop, and even calls
``locale.getlocale()`` to check the locale hasn't changed.  For the overwhelmingly
common case -- a format made only of the locale-independent numeric directives and
literal separators -- almost all of that work is avoidable.

Design: total backwards compatibility
-------------------------------------
The accelerator handles only this locale-independent subset::

    %Y %m %d %H %M %S %f %%   + literal text + whitespace

and it returns a value **only when its strict, greedy parse consumes the entire
input**.  For anything else -- an unsupported/locale-dependent directive, a format
it doesn't recognise, malformed input, leftover characters, non-ASCII text, a
dead-end that the stdlib regex would only resolve by backtracking -- it returns a
sentinel and the call falls through to the original ``_strptime``.  Because

* the parser mirrors each directive's exact regex (same greedy alternation order),
* a non-backtracking greedy parse that *completes* is provably the parse the regex
  engine also settles on (the regex only backtracks when greedy fails -- and then
  we defer instead), and
* the resulting ``datetime`` is built with the same ``cls(y, mo, d, ...)`` call, so
  invalid dates raise the identical ``ValueError``,

every input either gets a result byte-for-byte equal to stdlib's, or is handed to
stdlib unchanged.  The differential test-suite exercises this exhaustively, and
CPython's own ``test_strptime`` / ``test_datetime`` pass with it installed.
"""

from __future__ import annotations

import _strptime  # ty: ignore[unresolved-import]

__all__ = ["install", "strptime", "uninstall"]

# Directives whose field maps directly onto a datetime component with no
# locale dependence and no inter-field (julian/week/iso) computation.
_DIRECTIVES = frozenset("YmdHMSf")

# Module-level cache of compiled format token-lists. ``None`` marks a format the
# fast path does not support, so we never re-analyse it.
_format_cache: dict[str, list | None] = {}


def _compile_format(fmt):
    """Tokenise ``fmt`` into a parse plan, or return ``None`` to always defer."""
    tokens = []
    i = 0
    n = len(fmt)
    while i < n:
        c = fmt[i]
        if c == "%":
            if i + 1 >= n:
                return None  # trailing '%': let stdlib raise its canonical error
            d = fmt[i + 1]
            if d == "%":
                tokens.append(("lit", "%"))
            elif d in _DIRECTIVES:
                tokens.append((d, None))
            else:
                return None  # locale/unknown/unsupported directive: defer
            i += 2
        elif c.isspace():
            j = i
            while j < n and fmt[j].isspace():
                j += 1
            tokens.append(("ws", None))  # a run of whitespace matches r"\s+"
            i = j
        else:
            j = i
            while j < n and fmt[j] != "%" and not fmt[j].isspace():
                j += 1
            lit = fmt[i:j]
            if not lit.isascii():
                return None  # non-ASCII literal: case-fold semantics uncertain
            tokens.append(("lit", lit))
            i = j
    return tokens


def _parse(data, tokens):
    """Strict greedy parse; returns (y, mo, d, h, mi, s, us) or ``None`` to defer."""
    year = None
    month = day = 1
    hour = minute = second = micro = 0
    i = 0
    n = len(data)

    for kind, lit in tokens:
        if kind == "lit":
            L = len(lit)
            seg = data[i : i + L]
            # IGNORECASE semantics, ASCII-only (defer on any non-ASCII input slice).
            if len(seg) != L or not seg.isascii() or seg.lower() != lit.lower():
                return None
            i += L

        elif kind == "ws":
            j = i
            while j < n and data[j] in " \t\n\r\f\v":
                j += 1
            if j == i:
                return None  # r"\s+" needs at least one whitespace char
            i = j

        elif kind == "Y":  # (?P<Y>\d\d\d\d) -- exactly four ASCII digits
            seg = data[i : i + 4]
            if len(seg) != 4 or not (seg.isascii() and seg.isdigit()):
                return None
            year = int(seg)
            i += 4

        elif kind == "m":  # (?P<m>1[0-2]|0[1-9]|[1-9])
            a = data[i] if i < n else "\x00"
            b = data[i + 1] if i + 1 < n else "\x00"
            if a == "1" and b in "012":
                month = 10 + int(b)
                i += 2
            elif a == "0" and b in "123456789":
                month = int(b)
                i += 2
            elif a in "123456789":
                month = int(a)
                i += 1
            else:
                return None

        elif kind == "d":  # (?P<d>3[0-1]|[1-2]\d|0[1-9]|[1-9]| [1-9])
            a = data[i] if i < n else "\x00"
            b = data[i + 1] if i + 1 < n else "\x00"
            if a == "3" and b in "01":
                day = 30 + int(b)
                i += 2
            elif a in "12" and b.isdigit() and b.isascii():
                day = int(a + b)
                i += 2
            elif a == "0" and b in "123456789":
                day = int(b)
                i += 2
            elif a in "123456789":
                day = int(a)
                i += 1
            elif a == " " and b in "123456789":
                day = int(b)
                i += 2
            else:
                return None

        elif kind == "H":  # (?P<H>2[0-3]|[0-1]\d|\d| \d)
            a = data[i] if i < n else "\x00"
            b = data[i + 1] if i + 1 < n else "\x00"
            if a == "2" and b in "0123":
                hour = 20 + int(b)
                i += 2
            elif a in "01" and b.isdigit() and b.isascii():
                hour = int(a + b)
                i += 2
            elif a.isdigit() and a.isascii():
                hour = int(a)
                i += 1
            elif a == " " and b.isdigit() and b.isascii():
                hour = int(b)
                i += 2
            else:
                return None

        elif kind == "M":  # (?P<M>[0-5]\d|\d)
            a = data[i] if i < n else "\x00"
            b = data[i + 1] if i + 1 < n else "\x00"
            if a in "012345" and b.isdigit() and b.isascii():
                minute = int(a + b)
                i += 2
            elif a.isdigit() and a.isascii():
                minute = int(a)
                i += 1
            else:
                return None

        elif kind == "S":  # (?P<S>6[0-1]|[0-5]\d|\d)
            a = data[i] if i < n else "\x00"
            b = data[i + 1] if i + 1 < n else "\x00"
            if a == "6" and b in "01":
                second = 60 + int(b)
                i += 2  # cls() will raise, exactly as stdlib
            elif a in "012345" and b.isdigit() and b.isascii():
                second = int(a + b)
                i += 2
            elif a.isdigit() and a.isascii():
                second = int(a)
                i += 1
            else:
                return None

        elif kind == "f":  # (?P<f>[0-9]{1,6}) -- greedy, then right-pad to microseconds
            j = i
            limit = min(n, i + 6)
            while j < limit and "0" <= data[j] <= "9":
                j += 1
            if j == i:
                return None
            digits = data[i:j]
            micro = int(digits) * 10 ** (6 - (j - i))
            i = j

    if i != n:
        return None  # unconverted data remains: defer so stdlib raises canonically
    if year is None:
        year = 1900
    return (year, month, day, hour, minute, second, micro)


# The original stdlib implementation, captured at import time. This is both the
# fallback target for unsupported inputs and what uninstall() restores. The hook
# name is the CPython 3.14+ one; on older versions datetimium is simply inert.
_HOOK = "_strptime_datetime_datetime"
SUPPORTED = hasattr(_strptime, _HOOK)
_fallback = getattr(_strptime, _HOOK, None)
_saved = None  # set while installed, to the attribute value we replaced


def _fast_strptime_datetime(cls, data_string, format="%a %b %d %H:%M:%S %Y"):
    """Drop-in replacement for ``_strptime._strptime_datetime_datetime``."""
    try:
        tokens = _format_cache[format]
    except KeyError:
        tokens = _format_cache[format] = _compile_format(format)
    if tokens is not None:
        fields = _parse(data_string, tokens)
        if fields is not None:
            # Build exactly as stdlib does: cls(year, month, day, H, M, S, us).
            return cls(*fields)
    return _fallback(cls, data_string, format)  # ty: ignore[call-non-callable]


def strptime(data_string, format):
    """Convenience: accelerated ``datetime.datetime.strptime`` without install()."""
    import datetime as _dt

    if not SUPPORTED:
        return _dt.datetime.strptime(data_string, format)
    return _fast_strptime_datetime(_dt.datetime, data_string, format)


def install():
    """Patch ``_strptime`` so ``datetime.datetime.strptime`` takes the fast path.

    The C ``datetime.strptime`` looks up ``_strptime._strptime_datetime_datetime``
    on every call, so replacing that attribute is sufficient and is undone exactly
    by :func:`uninstall`.
    """
    global _saved
    if _saved is not None or not SUPPORTED:
        return
    _saved = getattr(_strptime, _HOOK)
    setattr(_strptime, _HOOK, _fast_strptime_datetime)


def uninstall():
    """Restore the original ``_strptime._strptime_datetime_datetime``."""
    global _saved
    if _saved is None:
        return
    setattr(_strptime, _HOOK, _saved)
    _saved = None
