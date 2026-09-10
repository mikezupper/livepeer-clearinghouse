# Reliability and operations

## Failure semantics

- Authorization fails closed with stable machine-readable reasons.
- Database writes use bounded transactions, timeouts, and idempotency keys.
- Kafka offsets advance only after the corresponding database transaction.
- Duplicate events are harmless; malformed events are quarantined; consumer
  lag and signer sequence gaps are observable.
- Authorization receipts are serialized by signer state and sequence. Pending
  receipts remain reserved until Kafka confirmation or explicit reconciliation.
- External adapter calls have deadlines, bounded retries where safe, circuit
  state, and distinct failure telemetry.

## Health

Liveness proves the process event loop is responsive. Readiness verifies
validated configuration, database access, required migrations, Kafka access,
and required adapters. Optional OAuth providers do not affect readiness when
disabled; enabled incomplete providers prevent startup.

## Residual signer-event risk

go-livepeer currently queues monitoring events asynchronously and may drop an
event if its in-process queue fills or Kafka retries are exhausted. The
reference deployment sizes the broker conservatively, alerts on producer error
metrics and sequence gaps, and correlates Kafka events with synchronous
authorization receipts. A later signer sequence confirms that the prior signed
state escaped even when its Kafka event did not. A missing final event remains
an unresolved conservative hold. Eliminating manual resolution of that final
case requires another trusted response observation or an upstream durable event
change.

## Operational artifacts

The [operations handbook](operations/index.md) covers monitoring, migration,
backup/restore, secret rotation, signer funding, broker recovery, poison events,
incident response, retention, capacity, and rollback. A runbook is evidence of
an intended response; only automated exercises and dated qualification records
prove that a particular release and topology meet it. Compose is a reproducible
reference and local exercise runtime, not a highly available production claim.
