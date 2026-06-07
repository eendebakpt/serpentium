"""``python -m serpentium`` -- inspect and control the startup autopatch.

python -m serpentium status     # show whether autopatch is active
python -m serpentium enable     # remove the disable-marker (default state)
python -m serpentium disable    # write a disable-marker next to the .pth
"""

from __future__ import annotations

import os
import sys

from ._autopatch import _MARKER_NAME, _marker_present, _should_autopatch


def _marker_path():
    # The .pth and its marker live at the site-packages root, one level above the
    # serpentium package directory.
    sp = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    return os.path.join(sp, _MARKER_NAME)


def _enable():
    path = _marker_path()
    try:
        os.remove(path)
        print(f"removed {path}")
    except FileNotFoundError:
        print("autopatch already enabled (no marker present)")


def _disable():
    path = _marker_path()
    with open(path, "w") as fh:
        fh.write("serpentium autopatch disabled\n")
    print(f"wrote {path}")


def _status():
    import serpentium

    print(f"autopatch active at startup: {_should_autopatch()}")
    print(f"disable-marker present:      {_marker_present()}")
    print(f"SERPENTIUM_AUTOPATCH env:    {os.environ.get('SERPENTIUM_AUTOPATCH', '<unset>')}")
    print(f"accelerator status:          {serpentium.status()}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "enable":
        _enable()
    elif cmd == "disable":
        _disable()
    elif cmd == "status":
        _status()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
