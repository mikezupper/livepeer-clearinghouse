from __future__ import annotations

import unittest

from deploy.smoke import signer_route_status_is_valid


class SmokeTests(unittest.TestCase):
    def test_signer_protocol_routes_accept_only_absent_or_live_signer_statuses(self) -> None:
        for path in (
            "/generate-live-payment",
            "/sign-orchestrator-info",
            "/discover-orchestrators",
        ):
            with self.subTest(path=path, status=502):
                self.assertTrue(signer_route_status_is_valid(path, 502))

        for path in ("/generate-live-payment", "/sign-orchestrator-info"):
            with self.subTest(path=path, status=405):
                self.assertTrue(signer_route_status_is_valid(path, 405))

        self.assertTrue(signer_route_status_is_valid("/discover-orchestrators", 200))
        self.assertTrue(signer_route_status_is_valid("/discover-orchestrators", 503))

        for path, status in (
            ("/generate-live-payment", 200),
            ("/generate-live-payment", 404),
            ("/sign-orchestrator-info", 503),
            ("/discover-orchestrators", 405),
            ("/unknown", 502),
        ):
            with self.subTest(path=path, status=status):
                self.assertFalse(signer_route_status_is_valid(path, status))
