# Kill-switch activation and reopening

Activation is appropriate whenever authorization, metering integrity, signer
custody, database authority, or exposure correctness is uncertain. Use the
operator-only `PUT /v1/operations/kill-switch` API with an explicit reason.
Confirm that new signer authorization is denied while liveness, readiness, and
authenticated read-only inspection remain available. Repeating the same PUT is
safe for authorization state, but this walking slice records each call as a new
audit event; do not claim request-level deduplication.

Reopening is a separate audited decision:

1. Identify and remediate the triggering incident.
2. Verify database and broker readiness, current schema, consumer heartbeat and
   checkpoint, signer identity/funding, adapter state, and recent verified
   backup.
3. Run reconciliation twice at a stable high-water mark. Require balanced facts,
   correct projections, and an explained disposition for every open integrity
   case. Pending value remains reserved.
4. Run the deterministic smoke and any component-specific recovery exercise.
5. Require a second deliberate operator confirmation and reason, then disable
   the kill switch. Watch authorization and settlement SLIs through the rollback
   window.

Abort reopening on degraded readiness, unexplained drift/gap/fork, stale
heartbeat, insufficient signer funding, unverified backup, missing incident
owner, or failed smoke. Never change database flags directly to bypass the audit
path.
