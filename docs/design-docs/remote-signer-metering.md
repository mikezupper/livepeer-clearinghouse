# Remote-signer metering without a go-livepeer patch

## Status

Accepted for the initial walking slice. Revisit if the pinned go-livepeer wire
contract, payment calculation, or Kafka producer changes.

## Decision

Use two observations from an unmodified, version-pinned go-livepeer remote
signer:

1. The authorization callback creates a synchronous **reservation receipt**
   before the updated payment state is signed.
2. A `create_signed_ticket` `GatewayEvent` on go-livepeer's mixed monitoring
   topic is the normal **settlement confirmation** and carries the
   signer-computed fee. Other event types on that topic are decoded only as a
   bounded generic envelope and ignored.

The clearinghouse correlates both observations by signer instance, signer
`StateID`, and `SequenceNumber`. The Kafka envelope UUID deduplicates transport
replays, but is not the business identity because go-livepeer generates it at
send time. `auth_id` identifies the clearinghouse session and remains stable
across the signer state. The webhook returns `expiry: 0` so every payment call
is observed.

The optional go-livepeer enhancement tracked in Beads is not a dependency of
this design.

## Why two observations

The callback happens after go-livepeer has calculated the payment and updated
its in-memory state, but immediately before it signs that state. Its body
contains headers and `RemotePaymentState`; it does not contain `computed_fee`,
the request manifest, ticket count, or the generated request ID.

The Kafka event is enqueued only after the state is signed, but before the
`RemotePaymentResponse` is constructed, encoded, or delivered to the caller. It includes
`computed_fee`, `num_tickets`, manifest, pipeline, duration, balance, state ID,
and sequence. Delivery is asynchronous through a bounded in-process queue.
Queue saturation or three exhausted Kafka write attempts drops the event.

Neither signal alone provides both synchronous exposure control and durable,
exact settlement. Together they provide a bounded hot path plus an explicit
reconciliation path.

## Supported payment shapes

The initial reference distribution accepts typed remote payment requests only:

| Payment type | Reservation quantity reconstructed at authorization |
| --- | --- |
| `live` | Ceiling of elapsed seconds; 10 seconds whenever elapsed time is non-positive |
| `lv2v` | Conservative ceiling of default signer frame size × FPS × elapsed seconds; 60 seconds whenever elapsed time is non-positive |
| `fixed` | One fixed unit |

Untyped `inPixels` payment requests are denied by the reference admission
policy because `inPixels` is absent from the authorization callback and cannot
be reconstructed safely. An adapter may support that shape only if it supplies
an independently authenticated estimate and passes the metering conformance
tests.

`LastUpdate` is parsed from RFC 3339 with nanosecond precision into an integer
epoch-nanosecond value. Python `datetime` and Pydantic datetime values retain
only microseconds and are not permitted for this calculation.

The Kafka `current_time`/`previous_time` values are RFC3339Nano strings and
their `*_unix` companions are signed int64 Unix milliseconds. The envelope
timestamp is also a signed Unix-millisecond decimal string. `billable_secs` is
a JSON number produced from `float64`; it is parsed as an exact decimal token
and bounded to ±9,223,372,036.854775807 before normalization. `gateway` and
`orch_url` may be empty and are never identity or authorization inputs.

The signer computes LV2V pixels using binary `float64` seconds and truncation.
The reservation deliberately uses exact integer nanoseconds and a conservative
ceiling, so it can be one or more pixels higher than the reported quantity. For
example, 140,625 ns at 720 × 1280 × 30 is exactly 3,888 pixels, while the
pinned Go float/truncation path produces 3,887. The reservation fee uses a
ceiling of the exact rational initial price. Settlement preserves the Kafka
`computed_fee` as reported and requires it to be no greater than the reserved
amount; an over-reservation is released. A reported fee above the reservation
is quarantined as an invariant violation. Ordinary bounded rounding variance
is not a fee mismatch.

## State machine

For each `(signer_id, state_id)`, authorization is serialized in PostgreSQL.

1. Validate the private signer callback and the bearer/session credential.
2. Reject expired, suspended, killed, unsupported, repeated, forked, or
   out-of-order state.
3. Reconstruct the request reservation for the supported payment type.
4. Atomically move that amount from lease `available` to `pending`; record the
   state snapshot and deterministic receipt ID; return HTTP 200 with a JSON
   decision, stable `auth_id`, and `expiry: 0`.
5. On Kafka arrival, deduplicate the envelope UUID, match the reservation
   receipt, validate the fee does not exceed the conservative reservation and
   validate the sequence, then atomically create usage, charge, ledger entries,
   and release unused reservation.
6. A later callback at sequence `n + 1` proves that the caller received a
   signer-produced state for sequence `n`. If its Kafka event is missing, mark
   receipt `n` as signer-confirmed and schedule reconciliation. Do not invent a
   second charge.

A callback that is allowed but followed by a signer failure can leave a
pending reservation. It expires into `unresolved`, not back into spendable
funds, unless a reconciliation policy proves that no signed response escaped.
This conservative hold bounds exposure at the cost of temporary
under-availability.

Session creation and refresh are idempotent operations keyed by the caller,
operation key, and a hash of the complete request. Because plaintext bearer
tokens are never persisted, a lost successful response is reported as a
conflict rather than minting a second live lease. The safe recovery flow is
list, revoke, and replace.

The authorization transaction takes the global exposure lock before the
account exposure and mutable scope rows, then the session/lease and a
transaction advisory lock for `(signer_id, state_id)`. Account cap, ledger
grant, policy, and rate-card publication use the same serialization boundary.
Deferred database constraints reconcile global/account projections with lease
holds and require receipt amount, immutable rate snapshot, and state-head
lineage to agree at commit.

Migration `20260909_0005` deliberately permits no settlement release. It
allows the authorization path to create pending receipts, record later-signer
confirmation, and quarantine a detected fork while keeping value held. The
metering migration replaces the lease and receipt transition guards in one
transaction with evidence-backed `unresolved` and `settled` transitions. This
is an explicit adapter seam: loosening the 0005 guards without the Kafka
evidence and ledger write is invalid.

## Event identity and ordering

- Raw transport deduplication key: `(producer_id, kafka_event_id)`.
- Reservation identity: `(signer_id, state_id, sequence_number)`.
- Canonical usage event ID: deterministic from the immutable reservation
  identity. Schema evolution never changes business identity.
- Every canonical usage event names its `reservation_id`. Its authoritative
  occurrence is the signed `data.current_time`, preserved as exact
  `source.signed_current_time` and signed epoch nanoseconds; the unsigned
  envelope timestamp and broker receive time are transport metadata only.
- A second non-identical observation for the same reservation identity is a
  fork and is quarantined.
- Kafka partition order is not trusted because the producer keys messages with
  random envelope UUIDs. Database sequence rules provide semantic ordering.
- The reference topic is dedicated to one configured signer trust binding and
  uses delete retention without compaction or Kafka transactions. Within that
  bounded deployment, visible data offsets are contiguous. The consumer seeks
  to PostgreSQL's durable checkpoint on assignment and fails loudly if the
  broker cannot return the required offset; an expired low watermark is
  accepted only with a durable retention-gap record.
- The Kafka message key must be the canonical envelope UUID. The payload's
  `gateway` metadata is never used as signer identity; signer identity comes
  only from the configured topic/ACL binding.
- Late confirmations are accepted while their receipt exists, including after
  lease expiry. Expiry prevents new authorization; it does not erase debt.

One go-livepeer Kafka event represents one successful signing request, which
may contain 1–100 tickets. The canonical event therefore represents a signed
request/batch and records `num_tickets`; it is not expanded into fabricated
per-ticket events.

## Guarantees and residual risk

This design guarantees that supported authorization requests reserve a
deterministic conservative amount against a lease before signing and that duplicate or
forked observations cannot create duplicate charges. It does not guarantee
that every signed response produces a Kafka event. A final signed request with
a lost Kafka event may remain unresolved until operator reconciliation because
there is no later state transition to confirm it.

Required telemetry includes Kafka producer error counters, consumer lag,
sequence gaps, pending-receipt age, unresolved reserved value, fee mismatches,
forks, and poison-event count. The admin application exposes these conditions
and never labels unresolved value as settled usage.

The browser/API inspection surface is read-only: `GET /v1/usage`,
`GET /v1/charges`, `GET /v1/operations/reconciliation`, and
`GET /v1/operations/metering-health`. Account filters and cursors are bounded
opaque identifiers and authorization applies tenant/account scope. The walking
slice deliberately exposes no HTTP ingest, acknowledgement, retry, release, or
repair mutation. Quarantine and reconciliation records are durable PostgreSQL
state; the transactional outbox is not advertised as a Kafka dead-letter
publisher until a dispatcher exists.

## Alternatives considered

- **Kafka alone:** simplest, but cannot enforce the synchronous lease bound and
  silently loses accounting data when the producer drops an event.
- **Authorization callback alone:** synchronous and correlatable, but cannot
  prove that signing succeeded or observe the signer-emitted computed fee.
- **Clearinghouse reverse proxy:** can observe complete requests and responses,
  but contradicts the blueprint's direct-to-signer path and adds a signing-path
  availability hop. It remains a valid future adapter, not the reference path.
- **On-chain redemption as primary usage:** too late and incomplete because
  probabilistic tickets that do not win are still part of expected network
  cost. It is useful only as a reconciliation signal.
- **Patch go-livepeer:** can produce the strongest contract, but is explicitly
  optional and outside the initial scaffold.
