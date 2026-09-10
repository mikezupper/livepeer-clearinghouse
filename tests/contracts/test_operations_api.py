"""Runtime-generated operational API contract checks."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def contract() -> dict[str, Any]:
    return json.loads((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))


class OperationsContractTests(unittest.TestCase):
    def test_operational_reads_document_auth_scope_and_bounded_pagination(self) -> None:
        paths = contract()["paths"]
        audit = paths["/v1/operations/audit-events"]["get"]
        adapters = paths["/v1/operations/adapters"]["get"]
        self.assertEqual(audit["operationId"], "listAuditEvents")
        self.assertEqual(audit["security"], [{"cookieAuth": []}])
        parameters = {parameter["name"]: parameter for parameter in audit["parameters"]}
        cursor_variants = parameters["cursor"]["schema"]["anyOf"]
        cursor = next(variant for variant in cursor_variants if variant.get("type") == "string")
        self.assertEqual(cursor["maxLength"], 512)
        self.assertEqual(parameters["limit"]["schema"]["minimum"], 1)
        self.assertEqual(parameters["limit"]["schema"]["maximum"], 200)
        self.assertEqual(adapters["operationId"], "listAdapters")
        self.assertEqual(adapters["security"], [{"cookieAuth": []}])
        adapter_schema = adapters["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(adapter_schema["type"], "array")
        self.assertTrue(adapter_schema["items"]["$ref"].endswith("/AdapterManifestResponse"))

    def test_control_plane_documents_security_and_mutation_boundaries(self) -> None:
        paths = contract()["paths"]
        reads = {
            "/v1/operations/status": "getOperationsStatus",
            "/v1/operations/jobs": "listOperationsJobs",
            "/v1/operations/projection-checks": "listProjectionChecks",
            "/v1/operations/legal-holds": "listLegalHolds",
        }
        for path, operation_id in reads.items():
            operation = paths[path]["get"]
            self.assertEqual(operation["operationId"], operation_id)
            self.assertEqual(operation["security"], [{"cookieAuth": []}])
        for path, operation_id in {
            "/v1/operations/reconcile": "reconcileProjections",
            "/v1/operations/retention": "runRetention",
        }.items():
            operation = paths[path]["post"]
            self.assertEqual(operation["operationId"], operation_id)
            names = {parameter["name"] for parameter in operation["parameters"]}
            self.assertTrue({"idempotency-key", "x-csrf-token", "och_session"} <= names)
            self.assertEqual(operation["security"], [{"cookieAuth": []}])
        place = paths["/v1/operations/legal-holds"]["post"]
        release = paths["/v1/operations/legal-holds/{hold_id}/release"]["post"]
        self.assertIn("x-csrf-token", {parameter["name"] for parameter in place["parameters"]})
        self.assertIn("hold_id", {parameter["name"] for parameter in release["parameters"]})

    def test_operation_input_schemas_are_closed_and_bounded(self) -> None:
        schemas = contract()["components"]["schemas"]
        reconcile = schemas["ReconcileBody"]
        self.assertFalse(reconcile["additionalProperties"])
        self.assertEqual(reconcile["properties"]["mode"]["pattern"], "^(check|repair)$")
        self.assertEqual(reconcile["properties"]["reason"]["maxLength"], 1000)
        retention = schemas["RetentionBody"]
        self.assertEqual(retention["properties"]["batch_size"]["minimum"], 1)
        self.assertEqual(retention["properties"]["batch_size"]["maximum"], 1000)
        hold = schemas["PlaceHoldBody"]
        self.assertEqual(hold["properties"]["review_at"]["format"], "date-time")


if __name__ == "__main__":
    unittest.main()
