# serpentium internals

For the user-facing summary see [README.md](README.md).

## Table of contents

- [Auto-enable at startup](#auto-enable-at-startup)
- [Testing against CPython's own suite](#testing-against-cpythons-own-suite)
- [Building from source](#building-from-source)
- [Layout](#layout)
- [The accelerators](#the-accelerators)
  - [randium — `random`](#randium--random)
  - [datetimium — `datetime.strptime`](#datetimium--datetimestrptime)
  - [htmlium — `html.escape` / `html.unescape`](#htmlium--htmlescape--htmlunescape)

## Auto-enable at startup

Installing serpentium drops a `serpentium-autopatch.pth` at the site-packages root.
At interpreter startup `site` executes its `import` line, which imports
`serpentium._autopatch`, which calls `serpentium.install()` — so the accelerators
are active in every process with no user `import`. The import is lazy (the compiled
backend is only pulled into processes where autopatch actually fires).

Opt-out mirrors floatium: `SERPENTIUM_AUTOPATCH=0` (env, wins over everything), or
`python -m serpentium disable` (writes a `serpentium-autopatch.disabled` marker next
to the `.pth`). `SERPENTIUM_AUTOPATCH_DEBUG=1` surfaces startup install failures,
which are otherwise swallowed so startup can never break. The Rust-backed accelerators
are gated on `HAVE_RUST_BACKEND` so a source checkout without a build still auto-enables
the pure-Python ones.

## Testing against CPython's own suite

`tools/cpython_suite.py` runs CPython's
`test` package with serpentium active in every regrtest worker — it injects a
backend-aware `usercustomize` on `PYTHONPATH`, so single-process and `-j` parallel
runs both work:

```bash
python tools/cpython_suite.py                 # curated, serpentium-affected subset
python tools/cpython_suite.py --all -- -j8     # entire suite, parallel
python tools/cpython_suite.py --no-install     # baseline for comparison
```

The curated subset includes `test_html` and `test_htmlparser` (the latter drives the
patched `html.unescape` through `HTMLParser`). `tests/test_cpython_parity.py` runs
`test_random` in-process as a fast pytest regression guard. Today the full curated set
— `test_random`, `test_datetime`, `test_strptime`, `test_uuid`, `test_statistics`,
`test_html`, `test_htmlparser` (1,850 tests) — all pass with serpentium installed,
alongside serpentium's own parity/fuzz suite.

## Building from source

Requires a Rust toolchain.

```bash
pip install maturin
maturin develop --release       # builds serpentium._native and installs editable
pytest                          # parity vs. stdlib + meta-package tests
python tools/cpython_suite.py            # run CPython's own tests under serpentium
python tools/generate_html_entities.py --check   # verify the html tables are current
python benchmarks/bench.py stdlib -o a.json       # benchmark vs stdlib (pyperf)
python benchmarks/bench.py serpentium -o b.json && python -m pyperf compare_to a.json b.json --table
```

If the Rust extension is not built, `serpentium.randium.HAVE_RUST_BACKEND` and
`serpentium.htmlium.HAVE_RUST_BACKEND` are `False`; those accelerators are skipped (the
pure-Python ones still load), and `randium.Random()` raises rather than silently using
a slow path. `src/html_entities.rs` is generated — after a CPython entity-table change,
regenerate it with `python tools/generate_html_entities.py`.


## The accelerators

Each is a true drop-in: `install()` patches the stdlib **in place** and snapshots what
it touches so `uninstall()` restores it exactly. The guiding principle is *accelerate
loops, not primitives* — a single Python→native call can't beat CPython's hand-tuned C
(same algorithm + call overhead), so the wins come from pushing whole loops into native
code; per-call primitives only tie.

### randium — `random`

Out-of-tree successor to CPython issues
[#131435](https://github.com/python/cpython/issues/131435) /
[#96163](https://github.com/python/cpython/issues/96163) (a native
`randint`/`randrange`/`_randbelow` path, declined upstream). It ports the Mersenne
Twister core from `Modules/_randommodule.c` and implements the bounded-integer
generators natively (`u64` fast path, `BigUint` fallback), staying **bit-for-bit
`seed`-compatible** — same seed → same stream, and `setstate` accepts a stdlib
`random.Random`'s 625-word state.

- **In-place patching.** `install()` patches the accelerated primitives onto the
  `random.Random` *class* object rather than swapping the class, so pre-existing
  instances and `from random import Random` references are covered too; the whole
  derived API (`randint`, `choice`, `sample`, `gauss`, …) rides the patched
  `_randbelow` via `self`. Only the six frozen singleton functions (`random`, `seed`,
  `getrandbits`, `getstate`, `setstate`, `shuffle`) are rebound explicitly.
- **Lazy state migration.** An instance built before the patch migrates its live C MT
  state bit-for-bit on first accelerated use (via the builtin `getstate`), continuing
  the stream mid-stream with no reseed.
- **Subclassing / SystemRandom.** The patched `_randbelow` is a hybrid: Rust fast path
  only when `getrandbits` is the stock one, else CPython's exact rejection loop driven
  by `self.getrandbits` — preserving `SystemRandom` and getrandbits-overriding
  subclasses (which `test_random` checks). An after-fork hook reseeds the singleton.
- **The wins.** `shuffle` runs its whole Fisher-Yates loop in Rust (**~8–11×**,
  bit-identical, RNG consumed in CPython's order); module-level `random.random()` is
  served by a hand-written vectorcall over the singleton's state (skipping the Python
  frame + PyO3 dispatch), so even this primitive **ties C** (~1.0×). `sample`/`choices`
  are the obvious next loop targets.

### datetimium — `datetime.strptime`

A **pure-Python** fast path for `datetime.strptime`, which is ~4–5 µs/call because the C
method defers to `_strptime` (per-call regex dict rebuild, directive loop, even a
`locale.getlocale()`). datetimium accelerates the locale-independent subset
`%Y %m %d %H %M %S %f %%` + literals + whitespace, patching
`_strptime._strptime_datetime_datetime`. Speedup **1.3–2.4×**.

It is **fully backwards compatible by construction**: it returns a value only when a
strict, non-backtracking greedy parse consumes the *entire* input; anything else
(locale-dependent/unknown directive, malformed input, leftover characters, non-ASCII, a
backtracking dead-end) falls through to stock `_strptime`. Each directive matcher mirrors
the exact stdlib regex, a completed greedy parse is provably the one the regex engine
settles on, and the result is built with the same `cls(year, month, …)` call so invalid
dates raise the identical `ValueError`. A 442,920-case differential fuzz found **zero**
divergences in result or exception message; `test_strptime`/`test_datetime` pass. (A Rust
version could reach ~15–30× but is deferred to keep the build and the compatibility
argument simple.)

### htmlium — `html.escape` / `html.unescape`

Out-of-tree counterpart to CPython issue
[#151024](https://github.com/python/cpython/issues/151024) / PR
[#151025](https://github.com/python/cpython/pull/151025), a C accelerator **closed as
not planned** on maintenance/timing grounds (`html.parser` still in flux, a unified
`xml.escape` unlanded). It reimplements both functions in Rust ([`src/html.rs`](src/html.rs)):
`escape` scans at the byte level (the five specials are single-byte ASCII) and returns
the input unchanged when there's nothing to escape; `unescape` makes a single pass,
using [`memchr`](https://docs.rs/memchr) to find each `&` and resolving it against
generated tables + the WHATWG numeric rules. Measured **~3.3×** (escape) and **~4.5×**
(unescape with entities); a no-entity `unescape` ties stdlib's memchr-backed `'&' in s`.

- **In-place vectorcall patching.** `install()` overwrites the `vectorcall` slot on the
  `html.escape`/`html.unescape` function objects (via `PyFunction_SetVectorcall`, the
  [copium](https://github.com/Bobronium/copium)/floatium technique) instead of rebinding
  names. Because object identity is preserved, `html.parser.unescape` — captured by
  `from html import unescape` at import — is accelerated for free, and `uninstall()` is a
  single pointer restore. The trampoline fast-paths only ordinary positional `str` calls;
  keyword args, non-`str` args, or a non-function target fall back to the stdlib (exact
  results and error messages).
- **Bit-for-bit compatibility.** The tables in
  [`src/html_entities.rs`](src/html_entities.rs) are generated from CPython's own
  `html.entities.html5` / `_invalid_charrefs` / `_invalid_codepoints` by
  [`tools/generate_html_entities.py`](tools/generate_html_entities.py), so htmlium can
  only diverge if those tables change. A differential fuzz finds **zero** divergences
  across all 2231 named references, the full numeric range (invalid/surrogate/overflow),
  and 60,000 random inputs; `test_html`/`test_htmlparser` pass.
