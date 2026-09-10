# Operations handbook

This directory is the production operations entry point. PostgreSQL is the
authority for identity, authorization, exposure, and accounting. Redpanda is a
transport and evidence channel, not a financial source of truth. The reference
Compose topology is single-node and is suitable for development, qualification,
and recovery exercises; it is not a highly available production topology.

Run `make help` before following a runbook. Mutating operations require the
explicit guard printed by the target. Never work around a failed guard, public
readiness check, reconciliation finding, or kill switch merely to restore
traffic.

## Runbook index

| Condition | First response | Runbook |
| --- | --- | --- |
| API latency, denial, or availability alert | Inspect bounded service-level telemetry and recent deploys | [Observability and SLOs](observability.md) |
| PostgreSQL unavailable or suspected loss | Stop admission, preserve evidence, select an isolated restore target | [Database backup and recovery](database-recovery.md) |
| Broker or consumer unavailable | Keep pending exposure reserved and recover from database checkpoints | [Broker recovery](broker-recovery.md) |
| Poison, gap, fork, or projection drift | Keep facts immutable, reconcile at a recorded high-water mark | [Metering integrity](metering-integrity.md) |
| Credential, webhook, backup, or signer key rotation | Use overlap where supported; stop admission for signer custody changes | [Secret and key rotation](secret-rotation.md) |
| Failed schema change or bad release | Stop admission and evaluate forward-fix versus verified rollback | [Migration and release rollback](migration-rollback.md) |
| Suspected security or privacy event | Preserve bounded evidence and contact the configured incident owner | [Incident response](incident-response.md) |
| Scheduled deletion or legal hold | Dry-run a bounded batch and verify hold exclusions | [Retention and legal holds](retention.md) |
| Global kill switch active | Resolve the cause, reconcile, and require an audited reopen reason | [Kill-switch reopening](kill-switch.md) |
| Capacity or saturation concern | Run only against a disposable project and compare the recorded baseline | [Capacity qualification](capacity.md) |
| Signer funding, custody, or private diagnostics | Keep the admin listener private and never automate funding | [Remote signer](signer.md) |
| Release restart and metering fault exercise | Use a uniquely named disposable Compose project | [Recovery qualification](recovery-qualification.md) |
| Release qualification evidence | Run the bounded aggregate and retain both sanitized manifests | [Release qualification evidence](qualification-evidence.md) |

## Required ownership and evidence

Production stores the fields in
[`deploy/operations-ownership.env.example`](../../deploy/operations-ownership.env.example)
as a non-secret contract in its deployment configuration system. Copy the
template outside the checkout, replace every placeholder, restrict changes to
the deployment owners, and verify it before a change or incident with
`make operations-ownership-check OPERATIONS_OWNERSHIP_FILE=/absolute/path/operations-ownership.env`.
The command reports only status and field count; retrieve contacts and the
incident system of record from that protected file, never from telemetry.

Each exercise records UTC start/end time, release and schema revisions,
operator, sanitized result, recovery-point age, recovery duration, and follow-up
Bead IDs using the [exercise record schema](exercise-record.md). Do not put email addresses, bearer material, database URLs, Kafka
payloads, or key paths in telemetry or exercise artifacts.

The target production objective is RPO at most five minutes and RTO at most
sixty minutes only for a deployment with separately tested encrypted base
backups and continuous WAL archiving. The reference logical-backup exercise
reports its measured values and makes no such guarantee.
