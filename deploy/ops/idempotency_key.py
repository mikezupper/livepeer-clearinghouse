"""Derive bounded operation child keys from an operator idempotency key."""

from __future__ import annotations

import hashlib
import re
import sys

KEY = re.compile(r"[A-Za-z0-9._:-]{16,200}")
SUFFIX = re.compile(r"[a-z][a-z0-9-]{0,31}")


def main() -> int:
    if len(sys.argv) != 3 or KEY.fullmatch(sys.argv[1]) is None:
        return 3
    if SUFFIX.fullmatch(sys.argv[2]) is None:
        return 3
    base, suffix = sys.argv[1:]
    candidate = f"{base}.{suffix}"
    if len(candidate) <= 200:
        print(candidate)
        return 0
    digest = hashlib.sha256(base.encode()).hexdigest()
    print(f"ops.{digest}.{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
