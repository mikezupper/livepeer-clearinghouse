#!/usr/bin/env python3
"""Enforce immutable, least-privilege GitHub supply-chain workflows."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PINNED_USE = re.compile(r"^\s*-\s+uses:\s+([^\s#]+)", re.MULTILINE)
FULL_SHA = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}$")

REQUIRED_WORKFLOWS = ("ci.yml", "security.yml", "scorecard.yml", "release.yml")
STALE_WORKFLOWS = ("bootstrap.yml", "builder-api.yml", "bump-version.yml")
RELEASE_IMAGES = {
    "ghcr.io/livepeer/clearinghouse-backend",
    "ghcr.io/livepeer/clearinghouse-admin-web",
    "ghcr.io/livepeer/clearinghouse-user-web",
    "ghcr.io/livepeer/clearinghouse-edge",
    "ghcr.io/livepeer/clearinghouse-ops",
    "ghcr.io/livepeer/clearinghouse-remote-signer",
}
SECURITY_CONTROLS = (
    "actions/dependency-review-action@",
    "github/codeql-action/init@",
    "language: [python, javascript-typescript]",
    "google/osv-scanner-action/osv-scanner-action@",
    "npm audit --package-lock-only --audit-level=high",
    "scanners: vuln,secret,misconfig,license",
)
RELEASE_CONTROLS = (
    "scripts/release_guard.py",
    "run: make test",
    "docker/build-push-action@",
    "image-ref: ${{ matrix.image }}@${{ steps.build.outputs.digest }}",
    "format: spdx-json",
    "format: cyclonedx-json",
    "actions/attest-build-provenance@",
    "actions/attest@",
    "cosign sign --yes",
    "cosign verify",
    "scripts/release_assets.py",
    "scripts/verify_release_descriptors.py",
    "subject-checksums: release/SHA256SUMS",
)
RELEASE_EXACT_COUNTS = {
    "environment: release": 2,
    "image: ${{ matrix.image }}@${{ steps.build.outputs.digest }}": 2,
    "subject-digest: ${{ steps.build.outputs.digest }}": 2,
    '"${IMAGE}@${DIGEST}"': 2,
    '"${image}@${digest}"': 2,
    "ref: ${{ needs.validate.outputs.tag }}": 3,
}
EXPECTED_WRITE_PERMISSIONS = {
    "ci.yml": {},
    "security.yml": {"security-events": 2},
    "scorecard.yml": {"id-token": 1, "security-events": 1},
    "release.yml": {"attestations": 2, "contents": 1, "id-token": 2, "packages": 2},
}


def validate(root: Path) -> list[str]:
    """Return violations for the repository rooted at *root*."""
    errors: list[str] = []
    workflow_dir = root / ".github" / "workflows"

    for name in REQUIRED_WORKFLOWS:
        if not (workflow_dir / name).is_file():
            errors.append(f"missing required workflow: {name}")
    for name in STALE_WORKFLOWS:
        if (workflow_dir / name).exists():
            errors.append(f"stale workflow must be removed: {name}")

    workflows = sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")))
    for workflow in workflows:
        source = workflow.read_text(encoding="utf-8")
        if "pull_request_target:" in source:
            errors.append(f"{workflow.name}: pull_request_target is forbidden")
        if "secrets." in source:
            errors.append(f"{workflow.name}: workflows must not consume repository secrets")
        for use in PINNED_USE.findall(source):
            if not FULL_SHA.fullmatch(use):
                errors.append(f"{workflow.name}: action is not pinned to a full SHA: {use}")
        if "pull_request:" in source and "secrets." in source:
            errors.append(f"{workflow.name}: pull-request workflow must not consume secrets")
        checkout_count = source.count("uses: actions/checkout@")
        if checkout_count != source.count("persist-credentials: false"):
            errors.append(f"{workflow.name}: every checkout must disable persisted credentials")
        if not re.search(r"(?m)^permissions:\n\s+contents: read\s*$", source):
            errors.append(f"{workflow.name}: top-level permissions must default to contents: read")
        if workflow.name != "release.yml" and re.search(r"(?m)^\s+contents: write\s*$", source):
            errors.append(f"{workflow.name}: contents write permission is forbidden")
        actual_write_permissions: dict[str, int] = {}
        for permission in re.findall(r"(?m)^\s+([a-z-]+): write\s*$", source):
            actual_write_permissions[permission] = actual_write_permissions.get(permission, 0) + 1
        if actual_write_permissions != EXPECTED_WRITE_PERMISSIONS.get(workflow.name, {}):
            errors.append(
                f"{workflow.name}: writable permissions differ from the least-privilege policy"
            )

    security = _read(workflow_dir / "security.yml")
    for control in SECURITY_CONTROLS:
        if control not in security:
            errors.append(f"security.yml: missing control: {control}")

    release = _read(workflow_dir / "release.yml")
    for image in sorted(RELEASE_IMAGES):
        if release.count(f"image: {image}") != 1:
            errors.append(f"release.yml: canonical image must appear once in build matrix: {image}")
    for control in RELEASE_CONTROLS:
        if control not in release:
            errors.append(f"release.yml: missing control: {control}")
    if release.count("docker/build-push-action@") != 1:
        errors.append("release.yml: release must contain one matrix build step")
    if ":sha-${{ needs.validate.outputs.revision }}" not in release:
        errors.append("release.yml: builds need an immutable commit staging tag")
    if "environment: release" not in release:
        errors.append("release.yml: publish job must use the protected release environment")
    if "    needs: [validate, qualify]" not in release:
        errors.append("release.yml: image build must depend on release qualification")
    if "    needs: [validate, build]" not in release:
        errors.append("release.yml: publish must depend on the qualified image build")
    if release.count("      contents: write") != 1:
        errors.append("release.yml: only publish may have contents write permission")
    for control, expected_count in RELEASE_EXACT_COUNTS.items():
        if release.count(control) != expected_count:
            errors.append(
                f"release.yml: expected {expected_count} exact occurrences of control: {control}"
            )
    component_checksums = (
        'sha256sum "${COMPONENT}.image.json" "${COMPONENT}.spdx.json" \\\n'
        '            "${COMPONENT}.cyclonedx.json" > "${COMPONENT}.sha256"'
    )
    if component_checksums not in release:
        errors.append(
            "release.yml: per-image checksums must cover descriptor, SPDX, and CycloneDX files"
        )
    publish = release[release.find("\n  publish:") :]
    existing_release_guard = publish.find('gh release view "$TAG"')
    mutation_positions = [
        position
        for marker in ("uses: docker/login-action@", "docker buildx imagetools create")
        if (position := publish.find(marker)) >= 0
    ]
    if (
        existing_release_guard < 0
        or not mutation_positions
        or existing_release_guard > min(mutation_positions)
    ):
        errors.append(
            "release.yml: existing-release guard must run before registry login or tag promotion"
        )
    oci_tag_guard = publish.find("- name: Refuse existing immutable OCI version tags")
    oci_tag_check = publish.find(
        'if docker buildx imagetools inspect "${image}:${VERSION}" >/dev/null 2>&1'
    )
    first_tag_mutation = publish.find("docker buildx imagetools create")
    if (
        oci_tag_guard < 0
        or oci_tag_check < oci_tag_guard
        or first_tag_mutation < 0
        or oci_tag_check > first_tag_mutation
    ):
        errors.append(
            "release.yml: all immutable OCI version tags must be checked before promotion"
        )

    return errors


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate(root)
    if errors:
        for error in errors:
            print(f"supply-chain: {error}", file=sys.stderr)
        return 1
    print("supply-chain: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
