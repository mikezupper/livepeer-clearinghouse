from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_schema(relative: str) -> dict[str, object]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class EventSchemaTests(unittest.TestCase):
    def test_raw_signer_schema_is_pinned_and_models_actual_batch_event(self) -> None:
        schema = load_schema("contracts/events/v1/go-livepeer-create-signed-ticket.schema.json")
        self.assertIn("e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6", schema["description"])

        data = schema["$defs"]["createSignedTicketData"]
        self.assertIn("num_tickets", data["required"])
        self.assertEqual(data["properties"]["num_tickets"]["maximum"], 100)
        self.assertEqual(
            data["properties"]["session_balance"]["pattern"],
            "^(0|[1-9][0-9]*)$",
        )

    def test_canonical_usage_schema_records_confirmation_provenance(self) -> None:
        schema = load_schema("contracts/events/v1/usage-event.schema.json")
        source = schema["properties"]["source"]
        self.assertIn("confirmation", source["required"])
        self.assertEqual(
            set(source["properties"]["confirmation"]["enum"]),
            {"kafka", "subsequent_signed_state", "direct"},
        )
        self.assertIn(
            "signer_sequence_reconciliation",
            source["properties"]["kind"]["enum"],
        )


if __name__ == "__main__":
    unittest.main()
