"""Secret-safe preflight for the Compose deployment boundary."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


class InvalidDeployment(ValueError):
    """An actionable error that never contains a secret value."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidDeployment(message)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(
            bool(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key)),
            f"environment line {number}: expected literal KEY=value",
        )
        if value[:1] in {"'", '"'}:
            require(
                len(value) >= 2 and value[-1] == value[0],
                f"environment line {number}: mismatched quotes",
            )
            value = value[1:-1]
        values[key] = value
    return values


def validate_production(env: dict[str, str]) -> None:
    if env.get("CLEARINGHOUSE_ENVIRONMENT") != "production":
        return
    user = env.get("POSTGRES_USER", "")
    require(bool(user and env.get("POSTGRES_DB")), "set production PostgreSQL identity fields")
    required = {
        "POSTGRES_PASSWORD_HOST_FILE",
        "CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE",
        "CLEARINGHOUSE_CREDENTIAL_PEPPER_HOST_FILE",
        "CLEARINGHOUSE_AUTH_RESEND_API_KEY_HOST_FILE",
        "CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET_HOST_FILE",
        "CLEARINGHOUSE_SIGNER_SESSION_PEPPER_HOST_FILE",
        "SIGNER_KEYSTORE_HOST_FILE",
        "SIGNER_PASSWORD_HOST_FILE",
    }
    if env.get("CLEARINGHOUSE_AUTH_GOOGLE_ENABLED") == "true":
        required.add("CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_SECRET_HOST_FILE")
    if env.get("CLEARINGHOUSE_AUTH_GITHUB_ENABLED") == "true":
        required.add("CLEARINGHOUSE_AUTH_GITHUB_CLIENT_SECRET_HOST_FILE")
    bootstrap = {
        "CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL_HOST_FILE",
        "CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET_HOST_FILE",
    }
    configured_bootstrap = {name for name in bootstrap if env.get(name)}
    require(
        not configured_bootstrap or configured_bootstrap == bootstrap,
        "configure both operator bootstrap host files or neither",
    )
    required.update(configured_bootstrap)
    for name in sorted(required):
        raw = env.get(name, "")
        require(bool(raw), f"set {name} to an operator-managed file outside the checkout")
        source = Path(raw).expanduser()
        require(not source.is_symlink(), f"{name} must not be a symlink")
        path = source.resolve()
        require(
            not path.is_relative_to(REPOSITORY),
            f"{name} must be outside the repository and Docker build context",
        )
        require(path.is_file(), f"{name} must be a regular file")
        metadata = path.stat()
        require(
            stat.S_IMODE(metadata.st_mode) in {0o400, 0o440, 0o600, 0o640},
            f"{name} must use mode 0400, 0440, 0600, or 0640",
        )
        require(
            metadata.st_uid in {0, os.getuid()},
            f"{name} must be owned by root or the deployment operator",
        )
        maximum = 1_048_576 if name == "SIGNER_KEYSTORE_HOST_FILE" else 65_536
        require(0 < metadata.st_size <= maximum, f"{name} must be 1..{maximum} bytes")
    password_path = Path(env["POSTGRES_PASSWORD_HOST_FILE"]).expanduser()
    password = password_path.read_text().rstrip("\n")
    require(
        len(password) >= 16
        and len(set(password)) >= 8
        and password != user
        and password.lower() not in {"clearinghouse", "postgres", "password"},
        "production PostgreSQL password file must contain a strong non-default value",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        validate_production(read_env(args.env_file))
    except (InvalidDeployment, OSError) as error:
        print(f"deployment preflight: {error}", file=sys.stderr)
        return 1
    print("deployment preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
