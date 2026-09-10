# Core beliefs and invariants

## Financial integrity

- Money and quantities use integer or exact decimal arithmetic in an explicit
  unit. Floating-point values never enter the ledger.
- Every charge references exactly one immutable usage event and price snapshot.
- Every usage event resolves to one lease; every lease records the balance and
  policy used when exposure was granted.
- Ledger entries are append-only, balanced, idempotent, and auditable.

## Exposure

- Authorization is governed by global, account, session, and lease controls.
- Concurrent requests use database serialization or atomic constraints; cached
  balances are never authoritative.
- Identity, policy, store, or ledger failure denies authorization distinctly.
- The kill switch denies new authorization while existing leases expire.
- The reference signer adapter reconstructs a deterministic reservation for
  supported typed payment requests from the callback state and pinned signer
  algorithm. Unsupported or ambiguous request shapes fail closed.
- Authorized value moves from lease availability to a pending reservation
  before signing. Missing confirmation never silently restores spendable value.

## Identity and privacy

- The core stores opaque identifiers and the minimum personal data needed for
  sign-in. Credential and OTP secrets are hashed at rest.
- Provider subject IDs, not mutable email addresses or usernames, anchor OAuth
  identities.
- Secrets, tokens, raw authorization headers, and signer keys never enter logs.

## Contracts and adapters

- HTTP and Kafka inputs are versioned and decoded before reaching domain code.
- Core behavior is independent of Pymthouse and every commercial provider.
- Built-in and external adapters pass the same conformance suite.
- Adapter incompatibility is a startup error, not a runtime surprise.

## Operability

- Health answers process liveness; readiness proves required dependencies and
  configuration are usable.
- Structured logs, metrics, and traces identify tenant-safe opaque IDs and
  idempotency keys.
- Upstream event loss, sequence gaps, poison events, and consumer lag are
  visible and actionable.
