# Database invariants v1

PostgreSQL is the fixed transactional store for the reference distribution.
Migrations may choose physical names, but they must enforce these constraints.

- Tenant-owned rows carry a non-null tenant identifier; account, principal,
  credential, lease, usage, charge, and audit queries are tenant-scoped.
- Every lease cites one account and the immutable policy/rate versions used
  when exposure was granted.
- `available + pending + settled <= cap` for each lease. Expiry prevents new
  reservations but never releases `pending` value.
- Reservation identity is unique on `(signer_id, state_id, sequence_number)`.
  It is independent of transport ID and schema version. Conflicting reuse is
  quarantined.
- Raw Kafka delivery is unique on `(producer_id, transport_event_id)`.
- Each canonical usage event cites exactly one reservation and lease. Each
  charge cites exactly one usage event and immutable price snapshot.
- Ledger transactions have at least two postings and sum to zero independently
  in every unit. Units cannot be mixed without an explicit conversion record.
- Idempotency keys are scoped by actor and operation; same key with a different
  canonical request hash is a conflict.
- Audit, usage, charge, grant, ledger posting, and outbox facts are append-only.
- Kafka offsets advance only in or after the database transaction that durably
  stores the observation outcome, including poison quarantine.
