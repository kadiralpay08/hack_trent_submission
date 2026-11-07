"""Play a game with your body, using one of the product variants.

  python play.py                          list the variants (none are active now: see variants/README.md)
  python play.py <variant>                dry run: prints the keys it would press, presses nothing
  python play.py <variant> --live         really press keys in whichever window has focus

The main way to play is now the glove and camera: punch_live.py (or play.bat). This script is the
variant framework, kept for trying a future version.

Hotkeys work from any window: F7 calibrate (press it, then get in guard: it counts down 5 s), F8 key output on or off (live starts off), F9 quit.
Each variant keeps its own settings, so choosing one never changes another. See variants/__init__.py
for how to add a variant.
"""
import argparse
import sys

import variants
from variants.base import DEFAULT_MAPPING


def list_variants():
    found, errors = variants.discover()
    for name in sorted(found):
        v = found[name]
        print(f"{name:20} {v.title}\n{'':20} {v.summary}\n{'':20} needs: {v.needs}")
    for name in sorted(errors):
        print(f"{name:20} FAILED TO LOAD: {errors[name]}")
    if not found and not errors:
        print("no variants found")
    return 0 if not errors else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == ["list"]:
        return list_variants()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("variant", help="which variant to play (python play.py list shows them)")
    parser.add_argument("--live", action="store_true", help="really press keys (default: only print them)")
    parser.add_argument("--mapping", default=DEFAULT_MAPPING, help="which game's key mapping to use")
    parser.add_argument("--camera", type=int, default=0, help="camera number (0 is the default webcam)")
    parser.add_argument("--max-seconds", type=float, help="stop by itself after this long (for testing)")
    # options that only one variant has are added after we know which variant was chosen
    known, _ = parser.parse_known_args(argv)
    try:
        variant = variants.get(known.variant)
    except ValueError as e:
        print(e)
        return 2
    variant.add_arguments(parser)
    args = parser.parse_args(argv)
    variant.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
