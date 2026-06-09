# serpentium

[![PyPI](https://img.shields.io/pypi/v/serpentium)](https://pypi.org/project/serpentium/)

**Drop-in performance accelerators for CPython, bundled.**

`pip install serpentium` and several hot paths in the standard library transparently start running on faster native code — no code changes, and no `import` required: serpentium auto-enables at interpreter startup.

```python
import random
from datetime import datetime
import html

deck = list(range(52))
random.shuffle(deck)                           # ~8x faster, served by Rust (randium)
datetime.strptime("2026-06-07", "%Y-%m-%d")    # faster, fully stdlib-compatible (datetimium)
html.unescape("Tom &amp; Jerry")               # ~4x faster, served by Rust (htmlium)
```

Serpentium aims for every accelerator to be a true drop-in: same results, down to
error messages. CPython's own tests for the modules it touches pass with serpentium
installed (`python tools/cpython_suite.py`).

## What's inside

| Element | Main target | Backend |
|---|---|---|
| **randium** | the `random` module — `random()`, `randint`/`randrange`/`_randbelow`, **`shuffle`** | Rust |
| **datetimium** | `datetime.strptime` (locale-independent formats) | Python |
| **htmlium** | `html.escape`, `html.unescape` (and `HTMLParser`, which calls `unescape`) | Rust |
| [**floatium**](https://pypi.org/project/floatium/) | `repr(float)`, `str(float)`, `f"{x:.3f}"`, `float(str)` | C++ (dep) |
| [**copium**](https://github.com/Bobronium/copium) | `copy.deepcopy` | dep |

`randium`, `datetimium` and `htmlium` ship inside serpentium; `floatium` and `copium`
are separately-maintained packages pulled in as dependencies.

## Benchmarks

CPython 3.14, release build. serpentium wins where a whole Python loop moves to
native code (shuffle, deepcopy, entity decoding); per-call primitives merely *tie*
CPython's hand-tuned C (the honest story — see [INTERNALS.md](INTERNALS.md)).

| Operation | stdlib | serpentium | speedup |
|---|--:|--:|--:|
| `random.shuffle(list[1000])` | 158 µs | 19 µs | **8.3×** |
| `copy.deepcopy` (pyperformance `bm_deepcopy`) | 7.8 µs | 1.3 µs | **6.0×** |
| `html.unescape` (text with entities) | 4.3 µs | 0.95 µs | **4.5×** |
| `html.escape` (clean paragraph) | 208 ns | 62 ns | **3.3×** |
| `datetime.strptime("%Y-%m-%d")` | 3.77 µs | 1.54 µs | **2.4×** |
| `f"{x:.3f}"` (float formatting) | 348 ns | 160 ns | **2.2×** |
| `datetime.strptime("%Y-%m-%d %H:%M:%S")` | 4.67 µs | 2.75 µs | **1.7×** |
| `random.sample(10k, 100)` | 20.7 µs | 17.5 µs | 1.2× |
| `random.randint(1, 6)` | 157 ns | 150 ns | 1.05× |
| `random.random()` | 54 ns | 51 ns | 1.04× |
| `html.unescape` (no entities) | 42 ns | 42 ns | 1.0× |

## Control

Auto-enabled by default. To opt out:

```bash
SERPENTIUM_AUTOPATCH=0 python ...     # per-process
python -m serpentium disable          # persistent (writes a marker file)
python -m serpentium status           # show what's active
```

Or drive it explicitly from code:

```python
import serpentium
serpentium.install(randium=True, datetimium=True, htmlium=True)   # toggle individually
serpentium.uninstall()                                            # restore the stdlib
```

## Documentation

Design, compatibility guarantees, the in-place-patching mechanism, the test strategy,
and **building from source** are in **[INTERNALS.md](INTERNALS.md)**.

## License

Dual-licensed under either [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at
your option.
