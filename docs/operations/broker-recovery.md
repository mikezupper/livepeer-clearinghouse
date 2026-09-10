# Broker and consumer recovery

## Failure behavior

When Redpanda or the consumer is unavailable, signer authorization remains
conservative: new admission may be stopped, existing unconfirmed receipts stay
pending, and no missing event restores spendable value. PostgreSQL checkpoints,
observations, quarantine rows, and reconciliation cases remain authoritative.

## Procedure

1. Stop new admission when heartbeat age, lag, or retention risk crosses the
   configured threshold. Record topic policy and database checkpoints.
2. Preserve broker logs and metadata without copying payload bodies into an
   incident artifact. Determine whether data is intact, truncated, compacted,
   or forked.
3. Restore broker availability. Provision a new topic when the old topic ever
   had multiple partitions, compaction, or lost required retention; changing a
   damaged topic back to `delete` cannot restore evidence.
4. Verify exactly one partition for the reference signer binding,
   `cleanup.policy=delete`, adequate positive retention, and the expected trust
   boundary. Production replication is normally three; the reference Compose
   broker deliberately uses one.
5. Start one consumer for the configured group. It seeks from PostgreSQL's
   durable checkpoint, treats re-delivery idempotently, quarantines conflicts,
   and records retention gaps rather than skipping them silently.
6. Run `make reconcile-check` and inspect oldest/amount pending plus every open
   case. Do not manually advance offsets or release reservations.

Expected result: fresh heartbeat, bounded lag, no unexplained gap/fork, and
stable reconciliation on a second pass. Abort and activate the kill switch if
broker low watermark is ahead of the database checkpoint without durable gap
evidence, topic policy is incompatible, or duplicate evidence disagrees.

Validate the reference path with
`make broker-recovery-test CONFIRM=disposable-only`. The target creates an
`och-ops-test-*` project, runs a synthetic smoke event, records its durable
checkpoint, stops and restarts Redpanda, recreates the consumer, runs another
event, requires checkpoint advancement, performs reconciliation, checks broker
health, and always removes the disposable volumes. It never uses a funded
signer. Record recovery time and the oldest unconfirmed receipt separately; the
target's JSON output contains only the before/after checkpoint offsets.
