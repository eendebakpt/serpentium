"""Micro-benchmarks comparing randium against the stdlib random module.

Run with::

    python benchmarks/bench_randium.py

This mirrors the benchmark from CPython issue #131435 (randint in a tight loop),
which is exactly the path randium pushes into Rust.
"""

import random
import sys
from itertools import repeat, starmap
from time import perf_counter

import serpentium.randium as randium

if not randium.HAVE_RUST_BACKEND:
    sys.exit("randium Rust backend not compiled; build with `maturin develop --release`")

N = 1_000_000


def time_it(label, fn, *, repeats=5):
    best = min(_run_once(fn) for _ in range(repeats))
    rate = N / best
    print(f"{label:<34} {best * 1e3:8.2f} ms   {rate / 1e6:6.2f} M ops/s")
    return best


def _run_once(fn):
    t0 = perf_counter()
    fn()
    return perf_counter() - t0


def main():
    std = random.Random(42)
    fast = randium.Random(42)

    print(f"randint(1, 6) x {N:,}  (the issue #131435 hot path)")
    a = time_it("stdlib random.randint", lambda: list(starmap(std.randint, repeat((1, 6), N))))
    b = time_it("randium.randint", lambda: list(starmap(fast.randint, repeat((1, 6), N))))
    print(f"  -> speedup: {a / b:.2f}x\n")

    print(f"_randbelow(1000) x {N:,}")
    a = time_it("stdlib _randbelow", lambda: [std._randbelow(1000) for _ in range(N)])
    b = time_it("randium _randbelow", lambda: [fast._randbelow(1000) for _ in range(N)])
    print(f"  -> speedup: {a / b:.2f}x\n")

    print(f"random() x {N:,}")
    a = time_it("stdlib random", lambda: [std.random() for _ in range(N)])
    b = time_it("randium random", lambda: [fast.random() for _ in range(N)])
    print(f"  -> speedup: {a / b:.2f}x\n")

    pool = list(range(1000))
    print(f"choice(pool[1000]) x {N // 10:,}")
    m = N // 10
    a = time_it("stdlib choice", lambda: [std.choice(pool) for _ in range(m)])
    b = time_it("randium choice", lambda: [fast.choice(pool) for _ in range(m)])
    print(f"  -> speedup: {a / b:.2f}x")

    # The standout case: shuffle runs its whole Fisher-Yates loop in Rust, so it
    # wins big (unlike the per-call primitives, which merely tie CPython's C).
    big = list(range(1000))
    reps = N // 1000
    print(f"\nshuffle(list[1000]) x {reps:,}")
    a = time_it("stdlib shuffle", lambda: [std.shuffle(big) for _ in range(reps)])
    b = time_it("randium shuffle", lambda: [fast.shuffle(big) for _ in range(reps)])
    print(f"  -> speedup: {a / b:.2f}x")


if __name__ == "__main__":
    main()
