"""Deterministic local target: only cache + fast mode together are broken."""

import os
import sys


def main() -> int:
    cache = os.environ.get("CAUSESCOPE_CACHE", "off") == "on"
    fast = os.environ.get("CAUSESCOPE_FAST", "off") == "on"
    if cache and fast:
        print("interaction bug: cached values bypass fast-mode validation", file=sys.stderr)
        return 23
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
