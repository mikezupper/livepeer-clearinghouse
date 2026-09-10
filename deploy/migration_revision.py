"""Validate and report bounded Alembic revision identifiers."""

from __future__ import annotations

import json
import re
import sys

REVISION = re.compile(r"(?:-1|base|[A-Za-z0-9][A-Za-z0-9_]{0,79})")
CHANGE_RECORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{7,199}")
ARTIFACT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


def validate(value: str) -> str:
    if not REVISION.fullmatch(value):
        raise ValueError("invalid migration revision")
    return value


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--artifact-id":
        if not ARTIFACT_ID.fullmatch(sys.argv[2]):
            print('{"status":"refused","error":"invalid_artifact_id"}')
            return 3
        print(sys.argv[2])
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--change-id":
        if not CHANGE_RECORD.fullmatch(sys.argv[2]):
            print('{"status":"refused","error":"invalid_change_record"}')
            return 3
        print(sys.argv[2])
        return 0
    if len(sys.argv) not in {2, 3}:
        return 3
    try:
        current = validate(sys.argv[1])
        expected = validate(sys.argv[2]) if len(sys.argv) == 3 else None
    except ValueError:
        print('{"status":"refused","error":"invalid_revision"}')
        return 3
    if expected is None:
        print(current)
    else:
        print(
            json.dumps(
                {
                    "status": "current" if current == expected else "revision_mismatch",
                    "current": current,
                    "expected": expected,
                },
                separators=(",", ":"),
            )
        )
    return 0 if expected is None or current == expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
