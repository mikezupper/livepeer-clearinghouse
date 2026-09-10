from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deploy.operations_ownership import FIELDS, load_contract


def valid_contract() -> str:
    return "\n".join(f"{field}=owned-{index}" for index, field in enumerate(sorted(FIELDS)))


class OperationsOwnershipTests(unittest.TestCase):
    def test_contract_requires_every_non_placeholder_field(self) -> None:
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "ownership.env"
        path.write_text(valid_contract(), encoding="utf-8")
        self.assertEqual(set(load_contract(path)), FIELDS)

        path.write_text(valid_contract().replace("owned-0", "REPLACE_ME", 1), encoding="utf-8")
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and repository script.
            [sys.executable, "deploy/operations_ownership.py", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("REPLACE_ME", result.stdout)

    def test_contract_rejects_unknown_duplicate_and_missing_fields(self) -> None:
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "ownership.env"
        for content in (
            valid_contract() + "\nUNKNOWN_FIELD=value",
            valid_contract() + "\nOPERATIONS_SERVICE_OWNER=duplicate",
            "\n".join(valid_contract().splitlines()[1:]),
        ):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_contract(path)
