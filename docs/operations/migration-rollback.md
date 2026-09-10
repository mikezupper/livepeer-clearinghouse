# Migration and release rollback

## Before deployment

Validate the ownership contract as described in the [operations index](index.md).
Record the image digests, current and target Alembic revisions, verified backup,
contract compatibility, and configured rollback owner. Stop if the backup restore has not
been exercised or the migration cannot preserve populated authoritative state.

## Failure decision

Prefer a forward fix when new writes may use the target schema or when downgrade
would discard/reinterpret authoritative data. A downgrade is allowed only when
the migration's populated-state guard passes and application binaries remain
contract compatible.

Inspect the deployed revision with `make migration-status`. To select a target,
read the repository migration immediately preceding the bad revision and its
`downgrade()` guard; never guess or use `stamp`. After testing the exact target
against an isolated restored backup, create a record in the configured external
incident/change system containing the reason, owner, evidence, and approvals.
Stop API and consumer writers and run:

```sh
make migration-downgrade CONFIRM=migration-downgrade \
  REVISION=<prior_revision> VERIFIED_BACKUP_ID=<catalog_artifact_id> \
  CHANGE_RECORD_ID=<external_change_record_id> \
  OPERATIONS_OWNERSHIP_FILE=/absolute/path/operations-ownership.env
```

The target rejects an invalid revision, placeholder ownership contract, missing
`restore_verified` catalog evidence for the named artifact, or running
API/consumer. Alembic's migration-specific
guard remains authoritative and may refuse populated-state downgrade. The
target finishes by emitting the current and selected target revisions as JSON.
It also echoes the bounded external record ID and backup artifact ID so the
external record can capture the exact machine output; the reason remains in
that access-controlled system and is not discarded by the command.
Run plain `make migration-status` after switching the deployment to the prior
compatible release; it then compares against that image's repository head.

1. Activate the kill switch and stop writers/consumers.
2. Preserve logs, trace/request IDs, schema revision, and sanitized database
   invariants.
3. Test the exact action on an isolated restored backup.
4. For rollback, run the guarded downgrade, then deploy the compatible prior
   application. Never use `stamp` to pretend a schema changed.
5. For forward repair, apply the reviewed migration and new image as one
   controlled change.
6. Verify migration head, ledger/exposure reconciliation, checkpoints,
   readiness, authentication, signer denial diagnostics, and smoke behavior.

Abort on any destructive downgrade warning, unbalanced facts, drift, missing
checkpoint, or contract mismatch. Restore from the verified encrypted backup if
the database cannot be made consistent. Reopen admission through the audited
kill-switch procedure only after the selected release passes validation.
