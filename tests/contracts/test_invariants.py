from __future__ import annotations

import unittest

from contracts.domain.v1.invariants import (
    ChargeLineage,
    LedgerPosting,
    ReservationStatus,
    assert_charge_lineage,
    assert_ledger_balanced,
    assert_reservation_transition,
    conservative_reservation_amount,
    conservative_reservation_units,
)
from contracts.ports.v1.protocols import PortName


class ContractInvariantTests(unittest.TestCase):
    def test_exactly_seven_replaceable_business_ports(self) -> None:
        self.assertEqual(len(PortName), 7)

    def test_ledger_balances_independently_by_unit(self) -> None:
        assert_ledger_balanced(
            (
                LedgerPosting("payer", -25, "wei"),
                LedgerPosting("network", 25, "wei"),
            )
        )
        with self.assertRaisesRegex(ValueError, "independently"):
            assert_ledger_balanced(
                (
                    LedgerPosting("payer", -25, "wei"),
                    LedgerPosting("network", 25, "usd_micros"),
                )
            )

    def test_charge_lineage_cannot_cross_lease_or_account(self) -> None:
        valid = ChargeLineage("ch_1", "use_1", "lease_1", "lease_1", "acct_1", "acct_1")
        assert_charge_lineage(valid)
        with self.assertRaisesRegex(ValueError, "same lease"):
            assert_charge_lineage(
                ChargeLineage("ch_1", "use_1", "lease_1", "lease_2", "acct_1", "acct_1")
            )

    def test_expiry_cannot_release_or_reopen_pending_reservation(self) -> None:
        assert_reservation_transition(ReservationStatus.PENDING, ReservationStatus.UNRESOLVED)
        with self.assertRaisesRegex(ValueError, "invalid reservation transition"):
            assert_reservation_transition(ReservationStatus.UNRESOLVED, ReservationStatus.PENDING)

    def test_lv2v_reservation_covers_go_float_truncation_counterexample(self) -> None:
        self.assertEqual(conservative_reservation_units("lv2v", 140_625), 3_888)
        self.assertEqual(conservative_reservation_amount(3_888, 1, 1), 3_888)

    def test_non_positive_elapsed_uses_signer_fallbacks_on_any_sequence(self) -> None:
        self.assertEqual(conservative_reservation_units("live", 0), 10)
        self.assertEqual(conservative_reservation_units("lv2v", -1), 1_658_880_000)


if __name__ == "__main__":
    unittest.main()
