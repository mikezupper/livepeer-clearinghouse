# Data classification and lifecycle v1

## Principles

Collect only data required for identity, authorization, accounting, operations,
and legal obligations. Store opaque internal identifiers in telemetry. Never
store raw OTPs, credential secrets, bearer tokens, OAuth authorization codes,
signer passwords, decrypted keys, or raw authorization headers.

The periods below are reference defaults, not legal advice. Operators configure
them before production according to jurisdiction and contractual obligations.
A shorter legal deletion requirement overrides a default unless an immutable
financial or security record must lawfully be retained.

This table is the lifecycle policy inventory, not an assertion that every row
has an automated deletion job. The walking slice automates only the categories
listed in the [retention runbook](../operations/retention.md); other rows require
deployment-specific implementation and verification before go-live.

## Classification and default retention

| Data | Class | Default | End-of-life action |
| --- | --- | --- | --- |
| OTP hash, challenge state, attempts | Restricted authentication | Expiry plus 24 hours, maximum 15 minutes usable | Delete rows; retain only non-identifying aggregate abuse metrics |
| OAuth state, nonce, PKCE verifier, temporary code data | Restricted authentication | One hour | Delete |
| Credential hash and prefix | Restricted authentication | Active lifetime plus 30 days after revoke | Delete hash; retain opaque credential ID in audit |
| Browser and signer session hashes | Restricted authentication | Expiry plus 30 days | Delete hash and network metadata; retain opaque audit references |
| Email and provider subject | Personal confidential | Account lifetime; delete request completed within 30 days | Delete or irreversibly tokenize unless legal hold applies |
| IP address and user-agent security events | Personal confidential | 90 days | Delete or aggregate/anonymize |
| Signer callback state snapshot | Confidential financial | Pending lifetime; settled snapshot 90 days | Reduce to receipt hash and required lineage fields |
| Raw Kafka payload and quarantine body | Confidential financial/operational | 30 days after settlement or resolution | Delete payload; retain hash, reason, IDs, and resolution audit |
| Usage, charge, grant, ledger, conversion snapshot | Financial record | Seven years | Delete only under approved jurisdictional schedule; preserve balanced lineage |
| Operator audit event | Security/financial record | Seven years | Append-only retention; legal-hold aware deletion after period |
| Application logs | Confidential operational | 30 days | Delete; security incident extracts follow security-event retention |
| Metrics and traces | Internal operational | 30 days detailed, 13 months aggregate | Delete detail; retain tenant-safe aggregates |
| Database backups | Restricted mixed | 35 days | Cryptographic erase by key destruction or verified media deletion |

## Deletion and anonymization

Account deletion first revokes active credentials and sessions and prevents new
leases. Personal profile fields are deleted or replaced by a one-way,
deployment-keyed tombstone. Financial, grant, usage, and audit rows keep opaque
tenant/account/principal identifiers so balances remain reproducible; they do
not retain email, provider username, IP address, or credential material.

Deletion is an idempotent, audited operation. It covers primary PostgreSQL,
search/read replicas, object exports, telemetry stores, and adapter-owned
copies. Backups age out on their fixed schedule; a restored backup must replay
the deletion tombstone log before serving traffic.

Legal holds are explicit records with approver, scope, reason, start, review
date, and release. They prevent deletion only for named categories and never
reactivate credentials or sessions.

## Backups and exports

Backups are encrypted with keys separate from database credentials, access is
logged, restoration is tested, and retention expiration is verified. Test and
development environments use synthetic identities and unfunded signer keys;
production data is not copied down.

User/admin exports contain encoded response-schema fields only, use short-lived
authorization, and are audit logged. Bulk exports require operator privilege
and a reason. Secrets, hashes, raw callback headers, and signer key material are
never exportable.

## Configuration and responsibility

Deployment configuration declares retention durations, legal jurisdiction,
privacy contact, security contact, backup region, encryption-key owner, and
incident owner. Production startup rejects placeholder contacts or retention
values outside supported safety bounds. The reference values remain available
for local evaluation without asserting regulatory compliance.

Beads `och-u8d.18` owns retention jobs, backup/restore, and incident runbooks;
`och-u8d.13` owns automated verification and secret/PII scanning.
