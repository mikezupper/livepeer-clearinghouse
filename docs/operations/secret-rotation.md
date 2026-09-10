# Secret and key rotation

## General rules

Rotate from file-backed secret sources. Never place values in Compose
configuration, command arguments, logs, Beads, or exercise artifacts. Record
only key IDs, actor, reason, start/end time, affected capability, and result.

The reference walking slice supports a disruptive, single-active-secret
procedure. It does not implement an in-process active/previous keyring.
Database `key_id` columns and append-only rotation events preserve the migration
seam, but recording an event does not switch a secret or make old hashes
readable. Until adapters receive configured key IDs, their rows remain tagged
`legacy`; do not claim cryptographic key selection from that tag. Deployments
requiring overlap must implement and test exact-key reads before using it.

Record each lifecycle transition with the same stable rotation ID and a unique
idempotency key:

```sh
make rotation-record CONFIRM=rotation-record \
  ROTATION_ID=auth-pepper-YYYYMMDD PURPOSE=auth_pepper ACTION=started \
  KEY_ID=auth-pepper-v2 PRIOR_KEY_ID=legacy \
  REASON='scheduled rotation' IDEMPOTENCY_KEY=auth-pepper-v2-started
```

Allowed purposes are `auth_pepper`, `credential_pepper`, `session_pepper`,
`backup_encryption`, and `signer_webhook`; allowed actions are `started`,
`activated`, and `retired`. The database rejects out-of-order or conflicting
transitions. Repeat the command with `ACTION=activated` after the new material
is live, then `ACTION=retired` only after its rollback window and verification
complete.

## Application hash keys

For authentication, API credentials, and signer-session material: record a
`started` rotation event, stop admission, revoke or expire material tied to the
old hash key, replace the relevant host secret file, recreate the affected
processes, and verify that old material fails while newly issued material works.
Record `activated`, observe through the rollback window, then record `retired`.
The reference retention job automates expired email challenges and browser
sessions only; credential and signer-session revocation remains an explicit
operator step. Do not rewrite an opaque secret into a retrievable form.

## Signer webhook

Activate the kill switch, replace the API and signer host files as one scheduled
change, recreate both processes, and run authenticated denial diagnostics.
There is no dual-secret overlap in this slice. Roll back both mounts together
while admission remains stopped if either side cannot authenticate or an
unexpected signer identity appears.

## Ethereum signer key

Activate the kill switch, stop admission, drain or reconcile pending receipts,
take and verify a backup, provision an encrypted V3 key outside the checkout,
fund gas/deposit/reserve deliberately, and run address/chain/funding diagnostics.
Switch mounts and address together. No script moves funds or creates plaintext
keys. Reopen only after a controlled funded-signing exercise and reconciliation.

## Backup recipient

The reference tooling accepts one active age recipient and does not re-encrypt
existing bundles. Stop the backup schedule, record `started`, replace the
recipient and key ID together, then create and restore-verify a new backup before
recording `activated`. Keep the old identity in the deployment's protected key
store until every retained bundle encrypted to it has expired or a separately
reviewed deployment tool has authenticated, re-encrypted, republished,
cataloged, and restore-verified each bundle. Only then record `retired`. Do not
manually overwrite a published bundle; the reference volume and restore job
deliberately reject that workflow. Loss of every valid identity is an
authoritative-data recovery incident.
