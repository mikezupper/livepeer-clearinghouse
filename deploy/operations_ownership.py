"""Validate the deployment-owned, non-secret operations ownership contract."""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

FIELDS = frozenset(
    {
        "OPERATIONS_BACKUP_REGION",
        "OPERATIONS_ENCRYPTION_KEY_OWNER",
        "OPERATIONS_INCIDENT_OWNER",
        "OPERATIONS_INCIDENT_SYSTEM_OF_RECORD",
        "OPERATIONS_LEGAL_JURISDICTION",
        "OPERATIONS_PRIVACY_CONTACT",
        "OPERATIONS_RETENTION_SCHEDULE_REVISION",
        "OPERATIONS_ROLLBACK_OWNER",
        "OPERATIONS_SECURITY_CONTACT",
        "OPERATIONS_SERVICE_OWNER",
    }
)
KEY = re.compile(r"[A-Z][A-Z0-9_]*")
PLACEHOLDERS = ("changeme", "example", "replace_me", "todo")


def load_contract(path: Path) -> dict[str, str]:
    """Load a literal env-format ownership contract without interpolation."""
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator or not KEY.fullmatch(key) or key not in FIELDS:
            raise ValueError(f"invalid field on line {number}")
        if key in values:
            raise ValueError(f"duplicate field on line {number}")
        parsed = shlex.split(raw_value, comments=False, posix=True)
        if len(parsed) != 1:
            raise ValueError(f"invalid value on line {number}")
        value = parsed[0].strip()
        lowered = value.casefold()
        if (
            not value
            or len(value) > 256
            or any(character.isspace() and character not in " " for character in value)
            or any(marker in lowered for marker in PLACEHOLDERS)
        ):
            raise ValueError(f"unsafe value on line {number}")
        values[key] = value
    missing = FIELDS.difference(values)
    if missing:
        raise ValueError("missing required fields: " + ",".join(sorted(missing)))
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        values = load_contract(args.path)
    except (OSError, ValueError) as error:
        print(f'{{"status":"refused","error":"{type(error).__name__}"}}')
        return 3
    print(f'{{"status":"ready","fields":{len(values)}}}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
