"""Focused contracts for the production account-to-charge qualification."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from deploy.qualification.journey import epoch_milliseconds, payment_state, signed_event

from clearinghouse.adapters.kafka import decode_go_livepeer
from clearinghouse.domain.metering import SignedTicketEvent


class JourneyContractTests(unittest.TestCase):
    def test_epoch_milliseconds_uses_exact_integer_time(self) -> None:
        value = datetime(2026, 9, 10, 12, 34, 56, 123000, tzinfo=UTC)
        self.assertEqual(epoch_milliseconds(value), 1_789_043_696_123)

    def test_signed_event_decodes_through_the_production_boundary(self) -> None:
        state = payment_state("state_qualification", "2026-09-10T12:34:56.123Z")
        event_id = "9c01ec22-b9f5-4d58-8040-1cfa379e2721"
        encoded = signed_event(event_id, state, "ssn_qualification")
        payload = json.loads(encoded)
        decoded = cast(SignedTicketEvent, decode_go_livepeer(encoded))
        self.assertEqual(payload["type"], "create_signed_ticket")
        self.assertEqual(decoded.transport_event_id, event_id)
        self.assertEqual(decoded.computed_fee, 1)
        self.assertEqual(decoded.pipeline, "fixed")
        self.assertEqual(decoded.auth_id, "ssn_qualification")
        self.assertEqual(decoded.current_time_unix_ms, 1_789_043_696_123)

    def test_make_and_browser_contract_use_live_stack_without_route_mocks(self) -> None:
        makefile = Path("Makefile").read_text()
        recipe = makefile.split("qualification-journey:", maxsplit=1)[1].split("\n\n", maxsplit=1)[
            0
        ]
        self.assertIn("deploy.qualification.journey", recipe)
        browser = Path("frontend/e2e/clearinghouse.live.spec.ts").read_text()
        self.assertNotIn("page.route(", browser)
        self.assertNotIn("mock-api", browser)
        self.assertIn("`${edge}/v1/`", browser)

    def test_journey_evidence_names_only_non_secret_outcomes(self) -> None:
        source = Path("deploy/qualification/journey.py").read_text()
        evidence = source[
            source.index('"authentication": "email_otp_operator_and_linked_holder"') :
        ]
        self.assertNotIn('"token"', evidence)
        self.assertNotIn('"secret"', evidence)
        self.assertNotIn('"code"', evidence)
