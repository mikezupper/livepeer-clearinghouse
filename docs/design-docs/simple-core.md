# Simplified core contracts

## Status

Accepted for `feat/simple-clearinghouse`. The production-platform implementation
that preceded this decision is preserved by the `PRE_SIMPLIFICATION_WORK` tag.

## Purpose

The core gives an authenticated Livepeer user a safe remote-signer credential,
discovers current network capabilities and advertised prices, and attributes
signer-reported usage and cost to an account and live-runner workload. It is not
a billing, credit, organization, marketplace, or compliance platform.

The primary client is `livepeer-python-gateway`. Workload traffic travels
directly between the SDK and the selected orchestrator or live runner. The core
participates only in authentication, discovery, payment authorization, and
metering.

## Authoritative identifiers

| Identifier | Owner | Meaning |
| --- | --- | --- |
| `user_id` | Core | One human identity with one verified primary email |
| `account_id` | Core | Usage and signer-access boundary; personal by default |
| `credential_id` | Core | Revocable SDK credential; plaintext is returned once |
| `workload_id` | Core | Stable correlation identity for one SDK workload |
| `price_observation_id` | Core | Immutable network offer observed before selection |
| `authorization_id` | Core | One permitted signer state and sequence |
| `usage_event_id` | Core | One normalized `create_signed_ticket` observation |
| `runner_session_id` | Live runner | Optional protocol alias attached to a workload |
| `manifest_id` | go-livepeer/live runner | Job/session alias present in signer evidence |
| `payment_session_id` | go-livepeer | Payment-state lineage alias |

Provider subjects anchor Google and GitHub identities. Verified email is the
human-facing account address, not an unverified authorization claim.

## Exact price and cost

Every price is the tuple `(numerator, denominator, currency, quantity_unit)`.
Numerators and quantities are non-negative integers and denominators are
positive integers. Binary floating-point values never enter the domain.

A price observation records orchestrator, capability, optional model and
constraints, exact price terms, `observed_at`, and `expires_at`. Starting a
workload snapshots the selected observation. It is immutable even when the
network later advertises different terms.

The listed-cost estimate is the ceiling of `quantity * numerator / denominator`.
Actual cost is the sum of matched signer `computed_fee` values. APIs and UIs
label these independently; a quote is not silently presented as settlement.

## Workload state

```text
active ──> expired (derived when the access deadline passes)
   ├─────> ended   (explicit terminal signal)
   └─────> revoked (explicit user cancellation)
```

Only an active account, SDK credential, workload token, and workload may
authorize payment. A workload is bound to one account, user, capability,
selected offer, maximum accepted price, and expiry. The authorization callback
records `(signer_id, state_id, sequence_number)` idempotently. It fails closed
when the state, token, price ceiling, capability, orchestrator, or global stop
does not agree.

This state describes signer access, not runner execution. Completion and failure
must come from an authenticated gateway or runner terminal event; the core never
infers success merely from expiry or the presence of billable usage.

The Kafka event is deduplicated by both its transport UUID and signer state
identity. It supplies measured quantity, ticket count, manifest/payment-session
aliases, and signer-computed fee. Evidence that cannot be correlated is retained
as `unmatched`; it never becomes another account's usage.

## Default storage and runtime

SQLite on a local persistent volume is the default authoritative store. The API
and Kafka consumer run in one supervised core service with one controlled writer
boundary. SQLite foreign keys and WAL mode are mandatory. Network filesystems
and horizontally replicated core processes are unsupported by this adapter.

Core workflows depend on repository and transaction behavior, not SQLite APIs.
A separately packaged PostgreSQL adapter may replace it only after passing the
same atomicity, uniqueness, pagination, idempotency, and restart conformance
suite. PostgreSQL-specific behavior is not retained in the default package.

All repository collection operations are bounded keyset pages. Each ordering
ends in a unique identifier, and adapters fetch one row beyond the requested
limit to determine whether continuation exists. HTTP cursors authenticate the
resource, caller scope, filters, ordering version, and discovery generation;
they never contain executable SQL. Aggregate summary methods are part of the
replaceable store contract so dashboard scale does not depend on a specific
database or on materializing every entity. The normative wire behavior is in
[`contracts/http/v1/pagination.md`](../../contracts/http/v1/pagination.md).

## Extension boundary

Only four build-time ports are public: `IdentityProvider`,
`NetworkDiscoveryProvider`, `AuthorizationPolicy`, and `CoreEventSink`. Built-in
email, optional OAuth, go-livepeer discovery, account-active policy, and Kafka
event publishing are wired explicitly at startup. Runtime annotation scanning
and environment-selected Python imports are prohibited.

Enterprise services consume versioned core events or HTTP APIs and own their
databases. They may implement organizations, budgets, billing, double-entry
accounting, OpenMeter export, retention, support, and compliance without writing
core tables or forking signer authorization.

## Explicit non-goals

- tenants, tenant administrators, invitations, or general RBAC;
- grants, credit balances, rate cards, markups, invoices, or collection;
- a double-entry ledger or financial reconciliation engine;
- custody beyond the unmodified pinned go-livepeer signer container;
- workload proxying, scheduling, or runner execution;
- arbitrary runtime plugins or shared extension access to the core database;
- transparent horizontal scaling while SQLite is selected.
