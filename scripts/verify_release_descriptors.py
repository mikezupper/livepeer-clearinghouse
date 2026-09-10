#!/usr/bin/env python3
"""Validate release image descriptors before adding public tags."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EXPECTED = {
    "backend": "ghcr.io/livepeer/clearinghouse-backend",
    "admin-web": "ghcr.io/livepeer/clearinghouse-admin-web",
    "user-web": "ghcr.io/livepeer/clearinghouse-user-web",
    "edge": "ghcr.io/livepeer/clearinghouse-edge",
    "ops": "ghcr.io/livepeer/clearinghouse-ops",
    "remote-signer": "ghcr.io/livepeer/clearinghouse-remote-signer",
}
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")


def validate_descriptors(directory: Path, *, version: str, revision: str) -> list[str]:
    """Return violations from the immutable image descriptor set."""
    errors: list[str] = []
    if REVISION.fullmatch(revision) is None:
        return ["expected revision must be a 40-character lowercase commit SHA"]
    paths = sorted(directory.glob("*.image.json"))
    expected_names = {f"{component}.image.json" for component in EXPECTED}
    actual_names = {path.name for path in paths}
    if actual_names != expected_names:
        errors.append(
            "descriptor set mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"unexpected={sorted(actual_names - expected_names)}"
        )

    digests: set[tuple[str, str]] = set()
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            errors.append(f"{path.name}: invalid JSON: {error}")
            continue
        if not isinstance(value, dict) or set(value) != {
            "component",
            "image",
            "digest",
            "revision",
            "version",
        }:
            errors.append(f"{path.name}: descriptor has an invalid field set")
            continue
        component = value["component"]
        image = value["image"]
        digest = value["digest"]
        if component not in EXPECTED or path.name != f"{component}.image.json":
            errors.append(f"{path.name}: component does not match its filename")
        elif image != EXPECTED[component]:
            errors.append(f"{path.name}: image is not canonical")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            errors.append(f"{path.name}: digest is not an exact sha256")
        elif isinstance(image, str) and (image, digest) in digests:
            errors.append(f"{path.name}: duplicate image digest")
        elif isinstance(image, str):
            digests.add((image, digest))
        if value["revision"] != revision:
            errors.append(f"{path.name}: revision does not match the release tag")
        if value["version"] != version:
            errors.append(f"{path.name}: version does not match the release tag")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    errors = validate_descriptors(args.directory, version=args.version, revision=args.revision)
    if errors:
        for error in errors:
            print(f"release-descriptor: {error}", file=sys.stderr)
        return 1
    print("release-descriptor: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
