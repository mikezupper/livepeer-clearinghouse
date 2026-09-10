# Metering integrity and projection reconciliation

Usage events confirm synchronous authorization receipts; they do not replace
them. Ledger postings, grants, usage, charges, receipts, and lease transitions
are immutable financial facts. Account/global exposure values are projections
that may be checked and repaired from those facts.

Run `make reconcile-check` first. The checker acquires a bounded job lock,
records a high-water mark, computes shadow balances/exposure and hashes, and
compares them with live projections without writing. Poison events, duplicate
transport IDs, sequence gaps/forks, fee mismatch, late confirmation, and
unresolved final receipts remain explicit cases.

If the check reports drift:

1. Activate the kill switch and stop admission.
2. Preserve the reconciliation run and immutable fact hashes.
3. Explain the source of drift. Unknown or contradictory facts require incident
   escalation, not repair.
4. In a verified backup and isolated clone, run the guarded repair target. It
   may update projections under the existing exposure serialization lock; it
   must never update or delete financial facts.
5. Re-run the check twice at a stable high-water mark, then validate balances,
   exposure, checkpoints, pending receipts, and open cases in production before
   using the reopen procedure.

Abort repair if the high-water mark changes unexpectedly, facts do not balance,
a hash differs between reads, or the repair would touch an immutable table.
Missing final signer confirmation stays conservatively reserved until another
trusted signal or an audited human resolution proves the correct outcome.
