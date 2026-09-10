#!/usr/bin/env python3
"""Fail closed unless a canonical repository tag matches the project release."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from pathlib import Path

CANONICAL_REPOSITORY = "livepeer/clearinghouse"
SEMVER_TAG = re.compile(
    r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9A-Za-z-][0-9A-Za-z-]*))*)?)$"
)


def validate_release(root: Path, *, repository: str, event: str, trigger_ref: str, tag: str) -> str:
    """Validate repository, trigger, version, and immutable git tag; return version."""
    if repository != CANONICAL_REPOSITORY:
        raise ValueError(f"publication is restricted to {CANONICAL_REPOSITORY}")
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        raise ValueError("release tag must be canonical SemVer prefixed with v")
    if event == "push":
        if trigger_ref != f"refs/tags/{tag}":
            raise ValueError("push release must be triggered by the validated tag")
    elif event == "workflow_dispatch":
        if trigger_ref != "refs/heads/main":
            raise ValueError("manual release recovery must be dispatched from main")
    else:
        raise ValueError("release event must be push or workflow_dispatch")

    version = match.group("version")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["version"] != version:
        raise ValueError(f"tag {tag} does not match pyproject.toml version {project['version']}")

    head = _git(root, "rev-parse", "HEAD^{commit}")
    tag_commit = _git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    if head != tag_commit:
        raise ValueError("checked-out commit does not equal the immutable release tag")
    return version


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(  # noqa: S603 -- arguments are constants or validated SemVer tags
        ["/usr/bin/git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--trigger-ref", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = validate_release(
        root,
        repository=args.repository,
        event=args.event,
        trigger_ref=args.trigger_ref,
        tag=args.tag,
    )
    revision = _git(root, "rev-parse", "HEAD^{commit}")
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"tag={args.tag}\nversion={version}\nrevision={revision}\n")
    print(f"release: validated {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
