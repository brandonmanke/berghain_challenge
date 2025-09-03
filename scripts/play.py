#!/usr/bin/env python3
import os
import sys


def _main() -> int:
    # Allow running without installing the package
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.abspath(os.path.join(here, "..", "src"))
    if src not in sys.path:
        sys.path.insert(0, src)
    from berghain.runner import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
