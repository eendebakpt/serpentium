#!/usr/bin/env python3
"""Benchmark serpentium against the stdlib with pyperf.

Each accelerator is measured with everything installed (``serpentium``) versus
nothing (``stdlib``). pyperf handles warmup, calibration and reports mean +- std.

    python benchmarks/bench.py stdlib     -o stdlib.json
    python benchmarks/bench.py serpentium -o serpentium.json
    python -m pyperf compare_to stdlib.json serpentium.json --table

Add ``--fast`` for a quick run, or ``--rigorous`` for publication-quality stats.
The README table is generated from a ``compare_to`` of the two runs.
"""

import copy
import random
from dataclasses import dataclass
from datetime import datetime

import pyperf
import serpentium


@dataclass
class _Rec:
    string: str
    lst: list
    boolean: bool


def add_cmdline_args(cmd, args):
    cmd.append(args.impl)


def main():
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.argparser.add_argument("impl", choices=["stdlib", "serpentium"])
    args = runner.parse_args()

    # Undo any startup autopatch first, so "stdlib" is the genuine stdlib and
    # "serpentium" is a clean install of everything.
    serpentium.uninstall()
    if args.impl == "serpentium":
        serpentium.install()

    import html  # bound after the mode is set

    deck = list(range(1000))
    pool = list(range(10_000))
    nested = {"list": [1, 2, 3, 43], "t": (1, 2, 3), "str": "hello", "subdict": {"a": True}}
    rec = _Rec("hello", [1, 2, 3], True)
    x = 3.141592653589793
    clean = "A perfectly ordinary sentence with no special characters here."
    ents = "caf&eacute; &amp; r&eacute;sum&eacute; &#x2014; 100&nbsp;&#37; &lt;tag&gt;"
    no_ents = "A perfectly ordinary sentence with no entity references here."
    t = runner.timeit

    t("random.shuffle(list[1000])", "f(d)", globals={"f": random.shuffle, "d": deck})
    t(
        "copy.deepcopy (bm_deepcopy)",
        "f(a); f(r)",
        globals={"f": copy.deepcopy, "a": nested, "r": rec},
    )
    t("html.unescape (entities)", "f(s)", globals={"f": html.unescape, "s": ents})
    t("html.escape (clean)", "f(s)", globals={"f": html.escape, "s": clean})
    t(
        'strptime("%Y-%m-%d")',
        "f(s, fmt)",
        globals={"f": datetime.strptime, "s": "2024-06-07", "fmt": "%Y-%m-%d"},
    )
    t('f"{x:.3f}"', "f'{x:.3f}'", globals={"x": x})
    t(
        'strptime("%Y-%m-%d %H:%M:%S")',
        "f(s, fmt)",
        globals={"f": datetime.strptime, "s": "2024-06-07 13:45:30", "fmt": "%Y-%m-%d %H:%M:%S"},
    )
    t("random.sample(10k, 100)", "f(p, 100)", globals={"f": random.sample, "p": pool})
    t("random.randint(1, 6)", "f(1, 6)", globals={"f": random.randint})
    t("random.random()", "f()", globals={"f": random.random})
    t("html.unescape (no entities)", "f(s)", globals={"f": html.unescape, "s": no_ents})


if __name__ == "__main__":
    main()
