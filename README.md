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

CPython 3.14. Measured with [`benchmarks/bench.py`](benchmarks/bench.py) (pyperf,
mean ± std dev); reproduce with `python benchmarks/bench.py stdlib -o a.json &&
python benchmarks/bench.py serpentium -o b.json && python -m pyperf compare_to a.json
b.json --table`. Absolute times and the marginal ratios depend on your CPU and CPython
build, so re-run it rather than trusting these exact figures.

serpentium wins where a whole Python loop moves to native code (shuffle, deepcopy,
entity decoding); per-call primitives merely *tie* CPython's hand-tuned C (the honest
story — see [INTERNALS.md](INTERNALS.md)).

| Operation | stdlib | serpentium | speedup |
|---|--:|--:|--:|
| `random.shuffle(list[1000])` | 157.8 ± 0.5 µs | 19.29 ± 0.04 µs | **8.2×** |
| `copy.deepcopy` (pyperformance `bm_deepcopy`) | 8.46 ± 0.07 µs | 1.33 ± 0.01 µs | **6.4×** |
| `html.unescape` (text with entities) | 4.49 ± 0.08 µs | 0.918 ± 0.003 µs | **4.9×** |
| `html.escape` (clean paragraph) | 206 ± 5 ns | 53.2 ± 0.4 ns | **3.9×** |
| `f"{x:.3f}"` (float formatting) | 334 ± 4 ns | 141 ± 1 ns | **2.4×** |
| `datetime.strptime("%Y-%m-%d")` | 3.65 ± 0.04 µs | 1.80 ± 0.03 µs | **2.0×** |
| `datetime.strptime("%Y-%m-%d %H:%M:%S")` | 4.57 ± 0.04 µs | 3.01 ± 0.02 µs | **1.5×** |
| `random.sample(10k, 100)` | 20.8 ± 0.1 µs | 18.1 ± 0.4 µs | 1.15× |
| `random.random()` | 41.5 ± 0.2 ns | 38.4 ± 0.2 ns | 1.08× |
| `random.randint(1, 6)` | 144 ± 1 ns | 140 ± 1 ns | 1.03× |
| `html.unescape` (no entities) | 33.6 ± 0.1 ns | 32.3 ± 0.3 ns | 1.04× |

Geometric mean: **2.3× faster** (std dev ≤ ~2% relative on every row).

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
