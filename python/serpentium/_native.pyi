"""Type stubs for the compiled Rust backend (serpentium._native).

One extension module hosts both native accelerators: the randium Mersenne-Twister
state and the htmlium html.escape/html.unescape functions.
"""

from __future__ import annotations

from types import FunctionType

# --- randium -----------------------------------------------------------------

class RandiumState:
    """Mersenne-Twister state, seed-compatible with CPython's _random.Random."""

    def __init__(self) -> None: ...
    def seed(self, a: int | None = ...) -> None: ...
    def random(self) -> float: ...
    def getrandbits(self, k: int) -> int: ...
    def randbelow(self, n: int) -> int: ...
    def randint(self, a: int, b: int) -> int: ...
    def shuffle(self, x: list) -> None: ...
    def getstate(self) -> list[int]: ...
    def setstate(self, st: list[int]) -> None: ...

def install_fast_random(func: object, rs: RandiumState) -> bool:
    """Point a Python function's vectorcall at the native random() over rs's state."""

def uninstall_fast_random() -> None:
    """Clear the fast random() path installed by install_fast_random."""

# --- htmlium -----------------------------------------------------------------

def escape(s: str, quote: bool = True) -> str:
    """Rust html.escape: replace & < > (and, if quote, " ') with HTML entities."""

def unescape(s: str) -> str:
    """Rust html.unescape: resolve named and numeric character references."""

def install(escape_fn: FunctionType, unescape_fn: FunctionType) -> bool:
    """Patch the two function objects' vectorcall slots in place.

    Returns False (a no-op) if either argument is not a plain Python function.
    """

def uninstall(escape_fn: FunctionType, unescape_fn: FunctionType) -> None:
    """Restore the original vectorcall slots saved by install()."""
