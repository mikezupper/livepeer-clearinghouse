from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from deploy.migration_revision import validate


class MigrationControlTests(unittest.TestCase):
    def test_revision_allowlist(self) -> None:
        for value in ("-1", "base", "0008_operability_lifecycle_seams"):
            with self.subTest(value=value):
                self.assertEqual(validate(value), value)
        for value in ("", "head;id", "../base", "x" * 81):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(value)

    def test_status_is_machine_readable_and_mismatch_is_nonzero(self) -> None:
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and repository script.
            [sys.executable, "deploy/migration_revision.py", "0007_operability", "0008_head"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stdout.strip(),
            '{"status":"revision_mismatch","current":"0007_operability","expected":"0008_head"}',
        )

    def test_change_record_identifier_is_bounded(self) -> None:
        for value, expected in (("INC-2026-1234", 0), ("x", 3), ("bad value 123", 3)):
            with self.subTest(value=value):
                result = subprocess.run(  # noqa: S603 -- fixed interpreter and script.
                    [sys.executable, "deploy/migration_revision.py", "--change-id", value],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, expected)

    def test_artifact_identifier_cannot_inject_output_or_sql(self) -> None:
        for value, expected in (("clearinghouse-20260910.dump.age", 0), ('x"}', 3)):
            with self.subTest(value=value):
                result = subprocess.run(  # noqa: S603 -- fixed interpreter and script.
                    [sys.executable, "deploy/migration_revision.py", "--artifact-id", value],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, expected)

    def test_makefile_guards_downgrade_and_stops_writers(self) -> None:
        source = Path("Makefile").read_text(encoding="utf-8")
        self.assertIn("CONFIRM=migration-downgrade", source)
        self.assertIn("VERIFIED_BACKUP_ID", source)
        self.assertIn("CHANGE_RECORD_ID", source)
        self.assertIn("operations-ownership-check", source)
        self.assertIn("ps --status running --services api consumer", source)

    def test_makefile_runs_populated_migration_matrix(self) -> None:
        source = Path("Makefile").read_text(encoding="utf-8")
        migration_recipe = source.split("test-migrations:", maxsplit=1)[1].split(
            "\ntest-migration-matrix:", maxsplit=1
        )[0]
        matrix_recipe = source.split("test-migration-matrix:", maxsplit=1)[1].split(
            "\n\ntest-admin-web:", maxsplit=1
        )[0]
        self.assertIn("test-migration-matrix", migration_recipe)
        self.assertIn("migration-fixture-matrix.sh", matrix_recipe)

    def test_populated_matrix_asserts_atomic_revision_and_row_preservation(self) -> None:
        source = Path("deploy/migration-fixture-matrix.sh").read_text(encoding="utf-8")
        for revision in (
            "20260909_0005",
            "20260909_0006",
            "20260910_0007",
            "20260910_0008",
        ):
            self.assertIn(revision, source)
        for protected_table in (
            "credentials",
            "signer_sessions",
            "metering_worker_heartbeats",
            "metering_transport_gaps",
            "operations_jobs",
        ):
            self.assertIn(protected_table, source)
        self.assertEqual(source.count("assert_downgrade_refused \\"), 3)
        self.assertIn('if [ "$after" != "$before" ]', source)
