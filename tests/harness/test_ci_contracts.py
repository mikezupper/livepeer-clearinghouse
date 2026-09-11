from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check_ci import validate

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


class CiContractTests(unittest.TestCase):
    def test_checked_in_workflow_is_valid(self) -> None:
        self.assertEqual(validate(SOURCE), [])

    def test_rejects_mutable_and_local_actions(self) -> None:
        mutable = SOURCE.replace(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@v7",
            1,
        )
        self.assertIn("action is not pinned to a full SHA: actions/checkout@v7", validate(mutable))
        local = SOURCE.replace(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "./checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            1,
        )
        self.assertIn(
            "local or non-GitHub action is forbidden: "
            "./checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            validate(local),
        )

    def test_rejects_privileged_or_unexpected_events(self) -> None:
        privileged = SOURCE.replace("  pull_request:\n", "  pull_request_target:\n")
        self.assertIn(
            "workflow triggers must be exactly: pull_request, push, workflow_dispatch",
            validate(privileged),
        )

    def test_rejects_workflow_and_job_permission_escalation(self) -> None:
        workflow = SOURCE.replace("  contents: read\n", "  contents: write\n", 1)
        self.assertIn("workflow must default to contents: read only", validate(workflow))
        job = SOURCE.replace(
            "  repository:\n", "  repository:\n    permissions:\n      contents: write\n", 1
        )
        self.assertIn(
            "job-level permissions are forbidden in required CI: repository", validate(job)
        )

    def test_rejects_missing_job_and_aggregate_dependency(self) -> None:
        omitted = SOURCE.replace("      - contracts\n", "", 1)
        self.assertIn(
            "required aggregate dependency mismatch: missing=['contracts'], unexpected=[]",
            validate(omitted),
        )
        renamed = SOURCE.replace("  contracts:\n", "  contracts-hidden:\n", 1)
        self.assertTrue(
            any(error.startswith("required job set mismatch:") for error in validate(renamed))
        )

    def test_rejects_weakened_aggregate_assertion(self) -> None:
        weakened = SOURCE.replace(
            'jq -e \'all(.[]; .result == "success")\' <<<"${NEEDS_JSON}"', "true", 1
        )
        self.assertIn(
            "required aggregate must assert every dependency succeeded", validate(weakened)
        )

    def test_rejects_make_target_relocated_to_another_job(self) -> None:
        relocated = SOURCE.replace("make quality-contracts", "make temporary-target", 1)
        relocated = relocated.replace("make quality-python", "make quality-contracts", 1)
        relocated = relocated.replace("make temporary-target", "make quality-python", 1)
        errors = validate(relocated)
        self.assertIn("job contracts must run exactly: make quality-contracts", errors)
        self.assertIn("job python-quality must run exactly: make quality-python", errors)

    def test_requires_visual_failure_artifacts(self) -> None:
        unconditional = SOURCE.replace("      - if: failure()\n", "      - if: always()\n", 1)
        self.assertIn(
            "browser visual job must retain artifacts only on failure", validate(unconditional)
        )
        missing_results = SOURCE.replace("          frontend/test-results/\n", "", 1)
        self.assertIn(
            "browser visual artifact is missing path: frontend/test-results/",
            validate(missing_results),
        )

    def test_rejects_inherited_project_references(self) -> None:
        self.assertIn(
            "stale inherited project references remain", validate(SOURCE + "\n# openmeter\n")
        )
