"""Parity tests: randium.Random must match CPython's random.Random exactly.

These are the load-bearing tests for the whole subpackage. If the Mersenne
Twister port or the bounded-integer logic drifts from CPython, the same seed
will stop producing the same sequence and these fail.
"""

import random

import pytest

randium = pytest.importorskip("serpentium.randium")

if not randium.HAVE_RUST_BACKEND:
    pytest.skip("randium Rust backend not compiled", allow_module_level=True)


SEEDS = [0, 1, 2, 42, 12345, 2**31, 2**64 + 7, 10**40, -5, -(2**70)]


@pytest.mark.parametrize("seed", SEEDS)
def test_random_stream_matches(seed):
    ref = random.Random(seed)
    new = randium.Random(seed)
    assert [ref.random() for _ in range(1000)] == [new.random() for _ in range(1000)]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("k", [1, 7, 8, 15, 16, 31, 32, 33, 53, 64, 65, 100, 256])
def test_getrandbits_matches(seed, k):
    ref = random.Random(seed)
    new = randium.Random(seed)
    assert [ref.getrandbits(k) for _ in range(200)] == [new.getrandbits(k) for _ in range(200)]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("n", [1, 2, 3, 6, 10, 1000, 2**20, 2**32, 2**32 + 1, 2**80])
def test_randbelow_matches(seed, n):
    ref = random.Random(seed)
    new = randium.Random(seed)
    assert [ref._randbelow(n) for _ in range(200)] == [new._randbelow(n) for _ in range(200)]


@pytest.mark.parametrize("seed", SEEDS)
def test_randint_matches(seed):
    ref = random.Random(seed)
    new = randium.Random(seed)
    for a, b in [(1, 6), (0, 1), (-10, 10), (1, 2), (0, 2**40), (-(2**62), 2**62)]:
        assert [ref.randint(a, b) for _ in range(100)] == [new.randint(a, b) for _ in range(100)], (
            a,
            b,
        )


@pytest.mark.parametrize("seed", SEEDS)
def test_randrange_matches(seed):
    ref = random.Random(seed)
    new = randium.Random(seed)
    cases = [(10,), (3, 10), (0, 100, 7), (100, 0, -3), (5, 5 + 2**40, 1)]
    for args in cases:
        assert [ref.randrange(*args) for _ in range(100)] == [
            new.randrange(*args) for _ in range(100)
        ], args


@pytest.mark.parametrize("seed", SEEDS)
def test_inherited_methods_match(seed):
    """choice/shuffle/sample/gauss ride on the overridden core -> must match."""
    pool = list(range(50))

    ref = random.Random(seed)
    new = randium.Random(seed)
    assert [ref.choice(pool) for _ in range(100)] == [new.choice(pool) for _ in range(100)]

    ref, new = random.Random(seed), randium.Random(seed)
    a, b = pool.copy(), pool.copy()
    ref.shuffle(a)
    new.shuffle(b)
    assert a == b

    ref, new = random.Random(seed), randium.Random(seed)
    assert ref.sample(pool, 10) == new.sample(pool, 10)

    ref, new = random.Random(seed), randium.Random(seed)
    assert [ref.gauss(0, 1) for _ in range(100)] == [new.gauss(0, 1) for _ in range(100)]


def test_state_interoperates_with_stdlib():
    """randium can adopt stdlib state and continue the identical stream."""
    ref = random.Random(99)
    for _ in range(37):
        ref.random()
    new = randium.Random()
    new.setstate(ref.getstate())
    assert [ref.random() for _ in range(500)] == [new.random() for _ in range(500)]


def test_string_seed_matches():
    ref = random.Random("serpentium")
    new = randium.Random("serpentium")
    assert [ref.random() for _ in range(100)] == [new.random() for _ in range(100)]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("n", [0, 1, 2, 3, 137, 1000])
def test_shuffle_rust_fastpath_matches(seed, n):
    """The whole-loop Rust shuffle must be bit-for-bit identical to stdlib."""
    ref_list = list(range(n))
    new_list = list(range(n))
    random.Random(seed).shuffle(ref_list)
    randium.Random(seed).shuffle(new_list)
    assert ref_list == new_list


def test_shuffle_falls_back_for_non_list():
    """Non-list mutable sequences must use the stdlib algorithm and still match."""

    class Seq:
        def __init__(self, data):
            self._d = list(data)

        def __len__(self):
            return len(self._d)

        def __getitem__(self, i):
            return self._d[i]

        def __setitem__(self, i, v):
            self._d[i] = v

    ref = list(range(60))
    random.Random(7).shuffle(ref)
    seq = Seq(range(60))
    randium.Random(7).shuffle(seq)
    assert seq._d == ref


def test_shuffle_falls_back_for_list_subclass():
    """list subclasses (possibly custom __setitem__) must not hit the Rust path."""

    class MyList(list):
        pass

    ref = list(range(60))
    random.Random(7).shuffle(ref)
    sub = MyList(range(60))
    randium.Random(7).shuffle(sub)
    assert list(sub) == ref
