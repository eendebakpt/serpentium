"""randium — a Rust backend for the Python ``random`` module.

This subpackage is the spiritual successor to CPython issue #131435 / PR #131436,
where a native (C) implementation of the ``randint`` / ``randrange`` / ``_randbelow``
code path was proposed and the ``random`` maintainer (Raymond Hettinger) declined to
take on the extra C complexity.

Instead of patching CPython, ``randium`` reimplements the Mersenne-Twister core *and*
the bounded-integer generators in Rust, while staying bit-for-bit ``seed``-compatible
with CPython's ``_random.Random`` -- so :class:`randium.Random` is a genuine drop-in
replacement whose hot integer/float path runs in Rust.

Usage::

    import random
    import serpentium.randium as randium

    randium.install()          # patch the stdlib ``random`` module in place
    random.randint(1, 6)       # now served by the Rust backend

    # or use the class directly
    rng = randium.Random(12345)
    rng.randint(1, 6)

How ``install()`` works
-----------------------
Rather than *replacing* ``random.Random`` with a subclass (which would miss any
``Random`` instance created -- or ``from random import Random`` reference taken --
before randium was imported), :func:`install` patches the accelerated primitives
**onto the existing ``random.Random`` class object in place**.  Because every
higher-level method (``randrange``, ``choice``, ``shuffle``, ``sample`` ...) and
every module-level convenience function reaches the core through ``self`` -- e.g.
``self._randbelow(...)`` -- a single in-place patch of the primitives propagates to:

* instances that already existed before ``install()``;
* ``from random import Random`` references that are instantiated later;
* the whole derived API, for free, via ordinary attribute dispatch.

Only the handful of module-level functions that are *frozen* bound methods of the
``random._inst`` singleton (``random.random``, ``random.getrandbits``, ``random.seed``,
``random.getstate``, ``random.setstate``) bypass class dispatch and must be rebound
explicitly; the flagship ``random.randint`` is rebound too so it takes the Rust fast
path.  :func:`uninstall` restores every patched attribute exactly.
"""

from __future__ import annotations

import contextlib
import os as _os
import random as _random
from _random import Random as _CRandom  # the builtin C base, for state migration
from hashlib import sha512 as _sha512
from operator import index as _index

__all__ = ["HAVE_RUST_BACKEND", "Random", "install", "uninstall"]

try:
    from serpentium._native import RandiumState as _RandiumState
    from serpentium._native import install_fast_random as _install_fast_random
    from serpentium._native import uninstall_fast_random as _uninstall_fast_random

    HAVE_RUST_BACKEND = True
except ImportError:  # pragma: no cover - exercised only on pure-Python installs
    _RandiumState = None  # ty: ignore[invalid-assignment]
    HAVE_RUST_BACKEND = False

_ONE = 1
_UINT64_MASK = (1 << 64) - 1


# ---------------------------------------------------------------------------
# Lazy Rust-state attachment
# ---------------------------------------------------------------------------
def _ensure_rs(self):
    """Return ``self._rs``, creating it lazily for instances that predate the patch.

    A :class:`random.Random` instance built before :func:`install` (or any instance
    not constructed through :class:`randium.Random`) carries a live C Mersenne-Twister
    state but no Rust mirror.  Migrate it bit-for-bit through the *builtin* ``getstate``
    -- never the possibly-patched Python one -- so the generated stream continues
    identically from wherever the instance left off.
    """
    rs = getattr(self, "_rs", None)
    if rs is None:
        rs = _RandiumState()
        rs.setstate(list(_CRandom.getstate(self)))
        self._rs = rs
    return rs


# ---------------------------------------------------------------------------
# The Rust-accelerated primitives.
#
# These are defined as plain functions so the exact same objects can be both
# (a) bound into the :class:`randium.Random` subclass below and (b) patched onto
# the stdlib ``random.Random`` by :func:`install`.
# ---------------------------------------------------------------------------
def _seed(self, a=None, version=2):
    """Faithful reproduction of ``random.Random.seed`` that feeds the Rust state.

    Mirrors CPython's seeding *exactly* (including the version-1 string hash and
    the version-2 SHA-512 expansion) so seed-compatibility is total, then routes
    the resulting integer / ``None`` into the Rust Mersenne-Twister.
    """
    if version == 1 and isinstance(a, (str, bytes)):
        a = a.decode("latin-1") if isinstance(a, bytes) else a
        x = ord(a[0]) << 7 if a else 0
        for c in map(ord, a):
            x = ((1000003 * x) ^ c) & _UINT64_MASK
        x ^= len(a)
        a = -2 if x == -1 else x
    elif version == 2 and isinstance(a, (str, bytes, bytearray)):
        if isinstance(a, str):
            a = a.encode()
        a = int.from_bytes(a + _sha512(a).digest())
    elif not isinstance(a, (type(None), int, float, str, bytes, bytearray)):
        raise TypeError(
            "The only supported seed types are: None,\nint, float, str, bytes, and bytearray."
        )

    # Now `a` is None, int, or (rarely) a float / leftover str under a non-1/2
    # version -- exactly the domain CPython's C-level seed accepts.  CPython seeds
    # a non-int hashable from (size_t)hash(a); reproduce that.
    seedval = a if a is None or isinstance(a, int) else hash(a) & _UINT64_MASK

    rs = getattr(self, "_rs", None)
    if rs is None:
        rs = self._rs = _RandiumState()
    rs.seed(seedval)
    self.gauss_next = None


def _random_meth(self):
    try:
        rs = self._rs
    except AttributeError:
        rs = _ensure_rs(self)
    return rs.random()


def _getrandbits(self, k):
    k = _index(k)
    if k < 0:
        raise ValueError("number of bits must be non-negative")
    try:
        rs = self._rs
    except AttributeError:
        rs = _ensure_rs(self)
    return rs.getrandbits(k)


def _randbelow(self, n):
    """``_randbelow_with_getrandbits`` -- Rust fast path, with a faithful fallback.

    When the instance uses the stock accelerated :func:`getrandbits` (the common
    case), the entire bounded draw runs in Rust.  When ``getrandbits`` has been
    *overridden* -- as ``random.SystemRandom`` does, or any user subclass -- the
    Rust shortcut would silently ignore that override, so we fall back to CPython's
    exact rejection-sampling loop driven by ``self.getrandbits``.  This preserves
    the subclassing contract that ``random``'s own test suite checks.
    """
    if not n:
        return 0
    if type(self).getrandbits is _getrandbits:
        try:
            rs = self._rs
        except AttributeError:
            rs = _ensure_rs(self)
        return rs.randbelow(n)
    # Overridden getrandbits: honour it (CPython's _randbelow_with_getrandbits).
    getrandbits = self.getrandbits
    k = n.bit_length()
    r = getrandbits(k)
    while r >= n:
        r = getrandbits(k)
    return r


def _shuffle(self, x):
    """``shuffle(x)`` -- whole-loop Fisher-Yates in Rust for plain lists.

    Unlike the per-call primitives (where one Rust call merely ties CPython's C),
    shuffling is a *Python loop driving N draws*; pushing the entire loop into Rust
    removes the interpreter overhead and runs ~8-11x faster while consuming the RNG
    in exactly CPython's order (so the result is bit-for-bit identical per seed).

    The fast path is taken only for an exact ``list`` on an instance using the
    stock Rust core; anything else (other mutable sequences, list subclasses with a
    custom ``__setitem__``, or subclasses overriding the RNG) uses CPython's exact
    algorithm so behaviour is unchanged.
    """
    if (
        type(x) is list
        and type(self).getrandbits is _getrandbits
        and type(self)._randbelow is _randbelow
    ):
        try:
            rs = self._rs
        except AttributeError:
            rs = _ensure_rs(self)
        rs.shuffle(x)
        return
    randbelow = self._randbelow
    for i in reversed(range(1, len(x))):
        j = randbelow(i + 1)
        x[i], x[j] = x[j], x[i]


def _getstate(self):
    try:
        rs = self._rs
    except AttributeError:
        rs = _ensure_rs(self)
    return (3, tuple(rs.getstate()), self.gauss_next)


def _setstate(self, state):
    version = state[0]
    if version == 3:
        version, internalstate, self.gauss_next = state
    elif version == 2:
        version, internalstate, self.gauss_next = state
        # In version 2, the state was stored as signed ints; normalise.
        try:
            internalstate = tuple(x % (2**32) for x in internalstate)
        except ValueError as e:
            raise TypeError from e
    else:
        raise ValueError(f"state with version {version} passed to Random.setstate() of version 3")
    rs = getattr(self, "_rs", None)
    if rs is None:
        rs = self._rs = _RandiumState()
    rs.setstate(list(internalstate))


# ---------------------------------------------------------------------------
# Standalone accelerated class (usable without install())
# ---------------------------------------------------------------------------
class Random(_random.Random):
    """Drop-in replacement for :class:`random.Random` backed by Rust.

    Subclasses the stdlib class so every higher-level method (``randrange``,
    ``choice``, ``shuffle``, ``sample``, ``choices``, ``gauss`` ...) is inherited
    unchanged and automatically rides on the Rust generator through the overridden
    primitives below.
    """

    seed = _seed
    random = _random_meth
    getrandbits = _getrandbits
    _randbelow_with_getrandbits = _randbelow
    _randbelow = _randbelow
    shuffle = _shuffle
    getstate = _getstate
    setstate = _setstate

    def __init__(self, x=None):
        if _RandiumState is None:
            raise RuntimeError(
                "the randium Rust backend is not compiled; build with maturin "
                "or use random.Random instead"
            )
        self._rs = _RandiumState()
        self.gauss_next = None
        self.seed(x)


# ---------------------------------------------------------------------------
# In-place monkeypatching of the stdlib ``random`` module
# ---------------------------------------------------------------------------

# Primitives patched onto the ``random.Random`` *class* object in place.  Every
# derived method/function reaches these through ``self`` and so is accelerated
# automatically -- no need to rebind them individually.
_CLASS_PATCHES = {
    "random": _random_meth,
    "getrandbits": _getrandbits,
    "seed": _seed,
    "getstate": _getstate,
    "setstate": _setstate,
    "_randbelow_with_getrandbits": _randbelow,
    "_randbelow": _randbelow,
    "shuffle": _shuffle,
}

# Module-level functions to rebind onto the ``random`` module.  The first five are
# *frozen* bound methods of the ``random._inst`` singleton that bypass class
# dispatch, so they must be rebound for the patch to take effect; ``shuffle`` is
# rebound so the flagship ``random.shuffle`` takes the whole-loop Rust fast path.
# Everything else derived (``randint``, ``randrange``, ``choice``, ``sample`` ...)
# rides on the patched ``_randbelow`` / ``random`` automatically via ``self``.
_MODULE_REBIND = ("random", "getrandbits", "seed", "getstate", "setstate", "shuffle")

_SENTINEL = object()
_saved = None
_fork_registered = False
_fast_random_fn = None  # keeps the native-vectorcall random.random alive while installed


def _reseed_after_fork():
    """Reseed the patched singleton's Rust state in the child after ``fork()``.

    The stdlib ``random`` module registers ``_inst.seed`` as an after-fork hook,
    but that captured the *original* (C) seed, which now only touches the unused
    C Mersenne-Twister state -- leaving the Rust state shared with the parent.
    Reseed it from fresh entropy here, but only while installed.
    """
    if _saved is None:
        return
    inst = getattr(_random, "_inst", None)
    if inst is not None:
        inst.seed()


def install():
    """Patch the stdlib ``random`` module in place to use the randium backend.

    Patches the accelerated primitives onto the existing ``random.Random`` class
    object (so pre-existing instances and ``from random import Random`` references
    are accelerated too) and rebinds the frozen module-level primitive functions.
    Call :func:`uninstall` to undo.
    """
    global _saved, _fork_registered
    if not HAVE_RUST_BACKEND:
        raise RuntimeError("randium Rust backend not available; cannot install()")
    if _saved is not None:
        return  # already installed

    if not _fork_registered and hasattr(_os, "register_at_fork"):
        _os.register_at_fork(after_in_child=_reseed_after_fork)
        _fork_registered = True

    klass = _random.Random

    saved_class = {}
    for name, fn in _CLASS_PATCHES.items():
        # Record the class's *own* attribute (not an inherited one) so uninstall
        # can either restore it or delete our override to re-expose the C base.
        saved_class[name] = klass.__dict__.get(name, _SENTINEL)
        setattr(klass, name, fn)

    inst = getattr(_random, "_inst", None)
    saved_module = {}
    for name in _MODULE_REBIND:
        saved_module[name] = getattr(_random, name, _SENTINEL)
        if inst is not None:
            setattr(_random, name, getattr(inst, name))

    # Skip the Python frame + PyO3 method dispatch for the hot module-level
    # random.random(): bind it to a native vectorcall over the singleton's Rust
    # state. The function body is a correct fallback if the slot isn't patched.
    global _fast_random_fn
    if inst is not None:
        rs = getattr(inst, "_rs", None) or _ensure_rs(inst)

        def _fast_random():
            return rs.random()

        if _install_fast_random(_fast_random, rs):
            _fast_random_fn = _fast_random
            _random.random = _fast_random  # ty: ignore[invalid-assignment]

    _saved = (saved_class, saved_module)


def uninstall():
    """Restore the stdlib ``random`` module to its original implementation."""
    global _saved, _fast_random_fn
    if _saved is None:
        return
    _uninstall_fast_random()
    _fast_random_fn = None
    saved_class, saved_module = _saved

    klass = _random.Random
    for name, val in saved_class.items():
        if val is _SENTINEL:
            # We added this (it was inherited from the C base); remove our copy.
            with contextlib.suppress(AttributeError):
                delattr(klass, name)
        else:
            setattr(klass, name, val)

    for name, val in saved_module.items():
        if val is _SENTINEL:
            with contextlib.suppress(AttributeError):
                delattr(_random, name)
        else:
            setattr(_random, name, val)

    _saved = None
