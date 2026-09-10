# Database backup and recovery

## Preconditions

PostgreSQL is authoritative. A backup operator needs read access to the source,
an age recipients file containing public recipients only, a protected output
directory, and the configured backup key ID. The restore operator additionally
needs an age identity and a brand-new isolated Compose project and volume.
Never restore over the source database.

## Backup

After configuring `CLEARINGHOUSE_BACKUP_AGE_RECIPIENT` and
`OPS_BACKUP_KEY_ID` in the selected environment file, run
`make backup CONFIRM=backup REASON='scheduled verification' \
IDEMPOTENCY_KEY=backup-YYYYMMDDTHHMMSSZ`. Optionally set
`BACKUP_RETENTION_DAYS=30` and an explicit safe basename with
`BACKUP_NAME=clearinghouse-YYYYMMDDTHHMMSSZ.dump.age`; otherwise the target
uses 30 days and generates one. The command derives bounded, deterministic
event keys from any valid operator idempotency key. The guarded job uses the
pinned PostgreSQL 18 client and streams
a `pg_dump` custom archive with a serializable, deferrable snapshot directly
into age encryption. It writes through a restricted temporary file, fsyncs,
atomically renames, and records the encrypted SHA-256, PostgreSQL and age
versions, Alembic revision, source LSN, time, and encryption key ID. It does not
record a password, URL, identity, or plaintext checksum. After publication, the
command also anchors a `created` event in PostgreSQL's append-only backup
catalog. If artifact publication succeeds while catalog recording is
unavailable, recover deliberately with
`make backup-record CONFIRM=backup-record BACKUP_NAME=<basename> \
REASON='catalog recovery' IDEMPOTENCY_KEY=backup-catalog-YYYYMMDDTHHMMSSZ`.

Expected result: a nonempty `.dump.age`, matching checksum sidecar, and an
append-only successful manifest. Abort and delete only the incomplete temporary
file on dump, encryption, fsync, or manifest failure. Do not delete the most
recent verified backup during cleanup.

## Isolated verification and restore

After configuring `CLEARINGHOUSE_BACKUP_AGE_IDENTITY_HOST_FILE`, select an
existing backup and run
`make restore-verify CONFIRM=isolated-restore BACKUP_NAME=<basename> \
REASON='scheduled restore verification' \
IDEMPOTENCY_KEY=restore-YYYYMMDDTHHMMSSZ`.
`BACKUP_NAME` is a basename in the configured backup volume, not an arbitrary
host path. The target refuses a nonempty or production target, verifies the
encrypted checksum, decrypts in a pipe, and uses
`pg_restore --single-transaction --exit-on-error` into a fresh database. Keep
admission closed. The reference verifier checks the recorded schema revision
and aggregate fact counts, balanced ledger, and account/global exposure. Before
any real cutover, additionally verify deletion tombstones newer than the
snapshot, reconciliation state, database checkpoints, and independently
recorded immutable-fact hashes when those are part of the deployment's recovery
evidence.

On success, the command appends `verified` and `restore-verified` events to the
source deployment's backup catalog. Treat the encrypted volume and database
catalog as separate production trust boundaries; the reference single-host
Compose layout demonstrates the protocol, but does not provide that separation.

Abort on checksum/decryption failure, schema ambiguity, missing tombstones,
unbalanced facts, unexplained projection drift, or a target that is not empty.
Destroy the isolated target after recording sanitized evidence. A real recovery
uses the same verification, updates DNS/service discovery deliberately, seeks
the consumer from authoritative database checkpoints, and reopens through the
kill-switch runbook.

Logical dumps do not meet the five-minute production RPO. Production operators
must separately configure encrypted base backups and continuous WAL archiving,
test point-in-time recovery, and measure both RPO and RTO.
