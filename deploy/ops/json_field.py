"""Extract one allowlisted scalar from a bounded operations JSON document."""

from __future__ import annotations

import json
import sys

FIELDS = {
    "artifact",
    "sha256",
    "key_id",
    "source_lsn",
    "retention_until",
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in FIELDS:
        return 3
    raw = sys.stdin.read(65537)
    if len(raw) > 65536:
        return 3
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return 3
    value = document.get(sys.argv[1]) if isinstance(document, dict) else None
    if not isinstance(value, str) or not value or "\n" in value:
        return 3
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
