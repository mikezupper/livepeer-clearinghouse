# Database invariants v1

SQLite is the authoritative store in the reference single-node distribution.
The API and Kafka consumer share one supervised process and serialize writes
through the `CoreStore` transaction boundary. Foreign keys, WAL mode, and a
bounded busy timeout are enabled at initialization.

- Each user owns one personal account. Account-owned queries derive scope from
  the authenticated user or SDK credential; clients cannot select another
  account identifier.
- OTP, browser-session, API-credential, OAuth-state, and workload secrets are
  stored only as keyed digests. API credentials and workload access values are
  revealed once.
- A price observation stores its runner, capability, optional model and
  constraints, exact rational price, observation time, and expiry. A workload
  copies that price tuple and retains the source observation identifier.
- Stored workload status is `active`, `ended`, or `revoked`; API projections
  report an active record as `expired` after its deadline. Only an active,
  unexpired workload can authorize signing. This access state does not claim a
  runner execution succeeded or failed without a trusted terminal event.
- Authorization identity is unique on `(signer_id, state_id,
  sequence_number)`. The workload ID is the stable go-livepeer `auth_id`, and a
  workload cannot be rebound to a different signer state.
- Raw signer delivery is unique on `transport_event_id`; normalized usage is
  also assigned a deterministic ID derived from signer and transport identity.
- Matched usage cites its authorization, workload, account, and user. Unknown
  evidence remains an `unmatched` usage record with those references absent; it
  is never attributed to another account.
- Quantities, exact prices, quote-derived cost, and signer-reported fees use
  non-negative integers and positive denominators. Binary floating point is not
  used.
- The global stop is a singleton state. When enabled, every new signer
  authorization fails closed.
- Kafka offsets are committed only after a relevant event has been decoded and
  ingested; unrelated messages are intentionally ignored and then committed.

Schema creation is idempotent and transactional. A replacement storage adapter
is selected explicitly by a deployment composition root and must pass the same
atomicity, uniqueness, scoping, pagination, idempotency, and restart conformance
suite. The core package contains no dormant PostgreSQL implementation.
