# Retention, deletion, and legal holds

The normative categories and defaults are in
[data lifecycle](../security/data-lifecycle.md). Financial facts and audit
records are not generic cleanup targets. A jurisdiction-specific approved
schedule and complete lineage proof are required before their deletion can be
enabled.

The reference walking slice implements three conservative categories only:
expired email-code challenges (`auth_ephemeral`), expired or revoked browser
sessions (`browser_sessions`), and stale metering-worker heartbeats
(`operational_detail`). OAuth transactions, credentials, signer sessions,
identity erasure, financial/audit records, payload reduction, and backup aging
remain policy requirements for a deployment that enables those data classes;
the reference job does not claim to delete them.

1. Run, for example,
   `make retention-dry-run CATEGORY=auth_ephemeral RETENTION_DAYS=1 BATCH_SIZE=200`.
   Record the selected category, derived cutoff, bounded scanned/deleted/held
   counts, and high-water mark. The command does not emit row bodies or source
   identifiers.
2. Validate and retrieve the privacy owner, jurisdiction, and schedule revision
   using the ownership contract command in the [operations index](index.md), then confirm the
   backup horizon, and active holds. A hold names its approver, scope, reason,
   review date, and release event.
3. Run one bounded batch with a stable retry key, for example
   `make retention-apply CONFIRM=retention CATEGORY=auth_ephemeral RETENTION_DAYS=1 BATCH_SIZE=200 REASON='approved schedule' IDEMPOTENCY_KEY=retention-20260910-auth-001`.
   The job serializes concurrent retention runs and selects candidates in a
   deterministic order. Reuse the same key only for the exact same request.
4. Verify the immutable job/run record and category counts, the secret-purge
   evidence for authentication rows, authentication behavior, ledger/exposure
   invariants, and a second dry-run. An active legal hold in the selected
   category conservatively holds the entire batch; narrower hold scopes are
   recorded but are not used to permit partial deletion in this slice.

The broader lifecycle table describes desired retention policy, not a claim
that every category has an automated erasure implementation. Identity deletion
must create a durable keyed tombstone before a deployment implements removal or
tokenization, and backups may age out only after the tombstone replay horizon
and verified replacement.

Abort if a legal hold matches, the batch is unbounded, the cutoff changes during
the run, a foreign-key or balance invariant would be weakened, or the job would
delete immutable financial/audit facts without an approved schedule.
