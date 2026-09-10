#!/usr/bin/env python3
"""Check that public documentation describes the repository that actually exists."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REPOSITORY = "https://github.com/livepeer/clearinghouse"
PROJECT_IMAGES = frozenset(
    {
        "ghcr.io/livepeer/clearinghouse-admin-web",
        "ghcr.io/livepeer/clearinghouse-backend",
        "ghcr.io/livepeer/clearinghouse-edge",
        "ghcr.io/livepeer/clearinghouse-ops",
        "ghcr.io/livepeer/clearinghouse-remote-signer",
        "ghcr.io/livepeer/clearinghouse-user-web",
    }
)
BADGES = {
    "Required quality": (
        f"{CANONICAL_REPOSITORY}/actions/workflows/ci.yml/badge.svg?branch=main",
        f"{CANONICAL_REPOSITORY}/actions/workflows/ci.yml",
        ".github/workflows/ci.yml",
    ),
    "Security": (
        f"{CANONICAL_REPOSITORY}/actions/workflows/security.yml/badge.svg?branch=main",
        f"{CANONICAL_REPOSITORY}/actions/workflows/security.yml",
        ".github/workflows/security.yml",
    ),
    "OpenSSF Scorecard": (
        f"{CANONICAL_REPOSITORY}/actions/workflows/scorecard.yml/badge.svg?branch=main",
        f"{CANONICAL_REPOSITORY}/actions/workflows/scorecard.yml",
        ".github/workflows/scorecard.yml",
    ),
    "Release": (
        f"{CANONICAL_REPOSITORY}/actions/workflows/release.yml/badge.svg",
        f"{CANONICAL_REPOSITORY}/actions/workflows/release.yml",
        ".github/workflows/release.yml",
    ),
    "License: MIT": (
        "https://img.shields.io/badge/license-MIT-blue.svg",
        "LICENSE",
        "LICENSE",
    ),
}
COMPOSE_ONLY_CONFIGURATION = frozenset(
    {
        "CLEARINGHOUSE_ADMIN_IMAGE",
        "CLEARINGHOUSE_BACKEND_IMAGE",
        "CLEARINGHOUSE_BACKEND_TEST_IMAGE",
        "CLEARINGHOUSE_EDGE_IMAGE",
        "CLEARINGHOUSE_OPS_IMAGE",
        "CLEARINGHOUSE_SIGNER_IMAGE",
        "CLEARINGHOUSE_TEST_GID",
        "CLEARINGHOUSE_TEST_UID",
        "CLEARINGHOUSE_USER_IMAGE",
        "OPS_BACKUP_RETENTION_UNTIL",
    }
)
DOCUMENTED_RUNTIME_CONFIGURATION = frozenset(
    {
        "CLEARINGHOUSE_AUTH_OAUTH_ATTEMPT_LIMIT",
        "CLEARINGHOUSE_AUTH_OAUTH_ATTEMPT_WINDOW_SECONDS",
        "CLEARINGHOUSE_AUTH_OTP_MAX_ATTEMPTS",
        "CLEARINGHOUSE_AUTH_OTP_SEND_LIMIT",
        "CLEARINGHOUSE_AUTH_OTP_SEND_WINDOW_SECONDS",
        "CLEARINGHOUSE_AUTH_OTP_TTL_SECONDS",
        "CLEARINGHOUSE_AUTH_OTP_VERIFY_LIMIT",
        "CLEARINGHOUSE_AUTH_OTP_VERIFY_WINDOW_SECONDS",
        "CLEARINGHOUSE_AUTH_SESSION_ABSOLUTE_TTL_SECONDS",
        "CLEARINGHOUSE_AUTH_SESSION_INACTIVITY_SECONDS",
        "CLEARINGHOUSE_AUTH_SESSION_TTL_SECONDS",
        "CLEARINGHOUSE_IDENTITY_INVITATION_TTL_SECONDS",
    }
)
REQUIRED_ROOT_DOCUMENTS = frozenset(
    {
        "README.md",
        "ARCHITECTURE.md",
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "GOVERNANCE.md",
        "LICENSE",
        "SECURITY.md",
        "SUPPORT.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    }
)
REQUIRED_ROOT_IGNORES = frozenset(
    {
        "/tmp/",  # noqa: S108 - this is a gitignore pattern, not a filesystem path
        ".idea/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        "htmlcov/",
        "**/coverage/",
        "**/dist/",
        "frontend/**/.vitest/",
        "frontend/**/*.tsbuildinfo",
        "frontend/**/tmp/",
        "frontend/playwright-report/",
        "frontend/test-results/",
        "frontend/tmp/",
    }
)
BADGE_RE = re.compile(r"\[!\[([^]]+)]\(([^)]+)\)]\(([^)]+)\)")
LINK_RE = re.compile(r"(?<!!)\[[^]]+]\(([^)]+)\)")
MAKE_TARGET_RE = re.compile(r"(?m)(?:^|`)make\s+(?:--no-print-directory\s+)?([a-z][a-z0-9-]*)")
MAKE_DEFINITION_RE = re.compile(r"(?m)^([a-zA-Z0-9_-]+):(?:[^=\n]|$)")
ENV_ASSIGNMENT_RE = re.compile(r"(?m)^(?:#\s*)?([A-Z][A-Z0-9_]*)=")
ENV_TOKEN_RE = re.compile(
    r"`((?:CLEARINGHOUSE|POSTGRES|SIGNER|LP_KAFKA|ETH_RPC_URL|OPS_BACKUP)"
    r"[A-Z0-9_]*)`"
)
COMPOSE_ENV_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
IMAGE_RE = re.compile(r"ghcr\.io/livepeer/clearinghouse-[a-z-]+")
REPOSITORY_RE = re.compile(
    r"https://github\.com/([^/\s)]+/(?:clearinghouse|livepeer-clearninghouse))",
    re.IGNORECASE,
)


def public_markdown_files(root: Path) -> tuple[Path, ...]:
    """Return public Markdown, excluding immutable source/reference material."""
    root_docs = sorted(root.glob("*.md"))
    docs = sorted(path for path in (root / "docs").rglob("*.md") if "references" not in path.parts)
    github_docs = sorted((root / ".github").rglob("*.md"))
    return tuple(root_docs + docs + github_docs)


def _clean_target(target: str) -> str:
    """Remove an optional Markdown title and angle brackets from a link target."""
    target = target.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    return target.split(maxsplit=1)[0]


def _local_link_errors(root: Path, documents: tuple[Path, ...]) -> list[str]:
    errors: list[str] = []
    for document in documents:
        source = document.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(source):
            target = _clean_target(raw_target)
            if not target or target.startswith(("#", "mailto:")) or "://" in target:
                continue
            relative = target.split("#", maxsplit=1)[0]
            if relative and not (document.parent / relative).resolve().exists():
                errors.append(f"broken local link in {document.relative_to(root)}: {target}")
    return errors


def _badge_errors(root: Path, readme: str) -> list[str]:
    errors: list[str] = []
    badges = {label: (image, target) for label, image, target in BADGE_RE.findall(readme)}
    if set(badges) != set(BADGES):
        errors.append(
            f"README badge set mismatch: expected={sorted(BADGES)}, actual={sorted(badges)}"
        )
    for label, (image, target, artifact) in BADGES.items():
        if badges.get(label) != (image, target):
            errors.append(f"README badge is not canonical: {label}")
        if not (root / artifact).is_file():
            errors.append(f"README badge has no backing workflow/artifact: {label} -> {artifact}")
    return errors


def _make_errors(root: Path, documents: tuple[Path, ...]) -> list[str]:
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    targets = set(MAKE_DEFINITION_RE.findall(makefile))
    errors: list[str] = []
    for document in documents:
        documented = set(MAKE_TARGET_RE.findall(document.read_text(encoding="utf-8")))
        for target in sorted(documented - targets):
            errors.append(
                f"undocumented Make implementation in {document.relative_to(root)}: {target}"
            )
    return errors


def _configuration_errors(root: Path, readme: str) -> list[str]:
    env_source = (root / ".env.example").read_text(encoding="utf-8")
    env_names = set(ENV_ASSIGNMENT_RE.findall(env_source))
    readme_names = set(ENV_TOKEN_RE.findall(readme))
    errors = [
        f"README configuration is absent from .env.example: {name}"
        for name in sorted(readme_names - env_names)
    ]

    compose_source = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in ("compose.yaml", "deploy/ops/restore.compose.yaml")
    )
    compose_names = set(COMPOSE_ENV_RE.findall(compose_source))
    unexplained = compose_names - env_names - COMPOSE_ONLY_CONFIGURATION
    errors.extend(
        f"Compose configuration is absent from .env.example: {name}" for name in sorted(unexplained)
    )
    errors.extend(
        f"documented runtime configuration is not wired through Compose: {name}"
        for name in sorted(DOCUMENTED_RUNTIME_CONFIGURATION - compose_names)
    )
    return errors


def _image_errors(root: Path) -> list[str]:
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    actual = frozenset(IMAGE_RE.findall(release))
    errors: list[str] = []
    if actual != PROJECT_IMAGES:
        errors.append(
            f"release image set mismatch: expected={sorted(PROJECT_IMAGES)}, "
            f"actual={sorted(actual)}"
        )
    for relative in ("docs/RELEASING.md", "docs/security/supply-chain.md"):
        documented = frozenset(IMAGE_RE.findall((root / relative).read_text(encoding="utf-8")))
        if documented != PROJECT_IMAGES:
            errors.append(
                f"canonical image set mismatch in {relative}: "
                f"expected={sorted(PROJECT_IMAGES)}, actual={sorted(documented)}"
            )
    return errors


def validate(root: Path) -> list[str]:
    """Return documentation/repository truth violations for ``root``."""
    errors: list[str] = []
    missing = sorted(
        relative for relative in REQUIRED_ROOT_DOCUMENTS if not (root / relative).is_file()
    )
    errors.extend(f"missing public project document: {relative}" for relative in missing)

    documents = public_markdown_files(root)
    errors.extend(_local_link_errors(root, documents))

    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else ""
    errors.extend(_badge_errors(root, readme))
    errors.extend(_make_errors(root, documents))
    errors.extend(_configuration_errors(root, readme))
    errors.extend(_image_errors(root))

    repository_surfaces = documents + tuple(sorted((root / ".github").rglob("*.yml")))
    public_source = "\n".join(path.read_text(encoding="utf-8") for path in repository_surfaces)
    folded = public_source.casefold()
    for repository in sorted(set(REPOSITORY_RE.findall(public_source))):
        if repository.casefold() != "livepeer/clearinghouse":
            errors.append(f"non-canonical clearinghouse repository reference: {repository}")
    for stale in ("mikezupper/livepeer-clearninghouse", "livepeer-pymthouse", "identity-webhook"):
        if stale in folded:
            errors.append(f"stale fork/template reference remains: {stale}")
    for stale_path in (
        ".github/REPO_SETUP.md",
        ".github/workflows/bootstrap.yml",
        ".github/workflows/builder-api.yml",
        ".github/workflows/bump-version.yml",
    ):
        if (root / stale_path).exists():
            errors.append(f"stale inherited setup artifact remains: {stale_path}")

    root_ignores = {
        line.strip()
        for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for pattern in sorted(REQUIRED_ROOT_IGNORES - root_ignores):
        errors.append(f"generated/editor artifact is not ignored: {pattern}")
    return errors


def main() -> int:
    """Validate documentation truth from this script's repository."""
    errors = validate(ROOT)
    for error in errors:
        print(f"docs: {error}", file=sys.stderr)
    if errors:
        return 1
    print("docs: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
