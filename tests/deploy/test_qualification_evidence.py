"""Focused contracts for release qualification evidence."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from deploy.qualification.evidence import (
    bounded_command_evidence,
    markdown_report,
    percentile_nearest_rank,
)


class QualificationEvidenceTests(unittest.TestCase):
    def test_nearest_rank_percentile_is_stable(self) -> None:
        self.assertEqual(percentile_nearest_rank([4.0, 1.0, 3.0, 2.0], 0.95), 4.0)
        self.assertEqual(percentile_nearest_rank([4.0, 1.0, 3.0, 2.0], 0.50), 2.0)
        with self.assertRaisesRegex(ValueError, "at least one"):
            percentile_nearest_rank([], 0.95)

    def test_command_evidence_is_bounded_and_does_not_include_output(self) -> None:
        result = subprocess.CompletedProcess(["test"], 0, "secret" * 20_000, "")
        evidence = bounded_command_evidence(result)
        self.assertEqual(evidence["exit_code"], 0)
        self.assertTrue(evidence["truncated"])
        self.assertNotIn("secret", evidence)
        self.assertRegex(str(evidence["output_sha256"]), r"^[0-9a-f]{64}$")

    def test_human_report_is_allow_listed_and_states_boundary(self) -> None:
        evidence = {
            "status": "passed",
            "capacity": {
                "workload": "readiness",
                "requests": 32,
                "concurrency": 1,
                "thresholds": {"p95_ms": 1000, "minimum_throughput_rps": 2.0, "max_errors": 0},
                "results": {"p95_ms": 2.0, "throughput_rps": 100.0, "errors": 0},
            },
            "compatibility": {"components": {"python": "Python 3.14.7"}},
            "durability": {
                "migration_cycle": {"status": "passed", "populated_guards": "verified"},
                "backup_restore": {"status": "passed"},
                "restart_and_metering": {"status": "passed"},
            },
            "raw_secret": "must-not-render",
        }
        report = markdown_report(evidence)
        self.assertIn("not a production capacity or sizing claim", report)
        self.assertIn("Encrypted isolated restore: passed", report)
        self.assertNotIn("must-not-render", report)

    def test_make_target_and_operator_document_are_wired(self) -> None:
        makefile = Path("Makefile").read_text()
        recipe = makefile.split("qualification-evidence:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("deploy.qualification.evidence", recipe)
        operations = Path("docs/operations/qualification-evidence.md").read_text()
        self.assertIn("make qualification-evidence", operations)
        self.assertIn("not a production sizing claim", operations)


if __name__ == "__main__":
    unittest.main()
