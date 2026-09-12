#!/usr/bin/env python3
"""Enforce immutable, least-privilege required CI structure."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"(?m)^\s*(?:-\s+)?uses:\s*([^@\s]+)@([^\s#]+)")
ACTION_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
ACTION = re.compile(rf"^{ACTION_SEGMENT}/{ACTION_SEGMENT}(?:/{ACTION_SEGMENT})*$")
JOB_HEADER = re.compile(r"(?m)^  ([a-z][a-z0-9-]*):\s*$")
NEED = re.compile(r"(?m)^      - ([a-z][a-z0-9-]*)\s*$")
MAKE = re.compile(r"(?m)^\s*- run:\s+make ([a-z][a-z0-9-]*)\s*$")
ALLOWED_TRIGGERS = frozenset({"pull_request", "push", "workflow_dispatch"})
JOB_TARGETS = {
    "backend": "test-backend",
    "frontend-quality": "quality-frontend",
    "frontend-tests": "test-frontend",
    "contracts": "quality-contracts",
    "browser-visual": "test-browser",
    "compose": "validate",
}


def _section(source: str, header: str) -> str | None:
    """Return one top-level YAML section without accepting nested lookalikes."""
    match = re.search(rf"(?m)^{re.escape(header)}:\s*$", source)
    if match is None:
        return None
    tail = source[match.end() :]
    following = re.search(r"(?m)^\S[^:]*:\s*(?:#.*)?$", tail)
    return tail if following is None else tail[: following.start()]


def _job_blocks(source: str) -> dict[str, str]:
    """Split the jobs mapping into top-level job bodies."""
    jobs = _section(source, "jobs")
    if jobs is None:
        return {}
    headers = list(JOB_HEADER.finditer(jobs))
    return {
        match.group(1): (
            jobs[match.end() : headers[index + 1].start()]
            if index + 1 < len(headers)
            else jobs[match.end() :]
        )
        for index, match in enumerate(headers)
    }


def _triggers(source: str) -> set[str]:
    """Return event names from the top-level on mapping."""
    events = _section(source, "on")
    if events is None:
        return set()
    return set(re.findall(r"(?m)^  ([A-Za-z_][A-Za-z0-9_]*):", events))


def validate(source: str) -> list[str]:
    """Return violations in the required CI workflow."""
    errors: list[str] = []
    triggers = _triggers(source)
    if triggers != ALLOWED_TRIGGERS:
        errors.append("workflow triggers must be exactly: " + ", ".join(sorted(ALLOWED_TRIGGERS)))

    permissions = _section(source, "permissions")
    if permissions is None or permissions.strip() != "contents: read":
        errors.append("workflow must default to contents: read only")

    for action, reference in USES.findall(source):
        if not ACTION.fullmatch(action):
            errors.append(f"local or non-GitHub action is forbidden: {action}@{reference}")
        if not SHA.fullmatch(reference):
            errors.append(f"action is not pinned to a full SHA: {action}@{reference}")

    jobs = _job_blocks(source)
    expected_jobs = set(JOB_TARGETS) | {"required"}
    if set(jobs) != expected_jobs:
        missing = sorted(expected_jobs - set(jobs))
        unexpected = sorted(set(jobs) - expected_jobs)
        errors.append(f"required job set mismatch: missing={missing}, unexpected={unexpected}")

    for job, target in JOB_TARGETS.items():
        body = jobs.get(job, "")
        if re.search(r"(?m)^    permissions:\s*$", body):
            errors.append(f"job-level permissions are forbidden in required CI: {job}")
        targets = MAKE.findall(body)
        if targets != [target]:
            errors.append(f"job {job} must run exactly: make {target}")

    visual = jobs.get("browser-visual", "")
    if not re.search(r"(?m)^      - if:\s*failure\(\)\s*$", visual):
        errors.append("browser visual job must retain artifacts only on failure")
    if not re.search(r"(?m)^        uses:\s*actions/upload-artifact@", visual):
        errors.append("browser visual job must upload failure artifacts")
    for artifact_path in ("frontend/test-results/", "frontend/playwright-report/"):
        if artifact_path not in visual:
            errors.append(f"browser visual artifact is missing path: {artifact_path}")

    required = jobs.get("required", "")
    if re.search(r"(?m)^    permissions:\s*$", required):
        errors.append("job-level permissions are forbidden in required CI: required")
    if not re.search(r"(?m)^    if:\s*always\(\)\s*$", required):
        errors.append("required aggregate must always run")
    needs = set(NEED.findall(required))
    if needs != set(JOB_TARGETS):
        missing = sorted(set(JOB_TARGETS) - needs)
        unexpected = sorted(needs - set(JOB_TARGETS))
        errors.append(
            f"required aggregate dependency mismatch: missing={missing}, unexpected={unexpected}"
        )
    assertion = 'jq -e \'all(.[]; .result == "success")\' <<<"${NEEDS_JSON}"'
    if assertion not in required:
        errors.append("required aggregate must assert every dependency succeeded")

    if "identity-webhook" in source or "openmeter" in source:
        errors.append("stale inherited project references remain")
    return errors


def main() -> int:
    """Validate the checked-in workflow."""
    errors = validate(WORKFLOW.read_text(encoding="utf-8"))
    for error in errors:
        print(f"ci: {error}")
    if errors:
        return 1
    print("ci: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
