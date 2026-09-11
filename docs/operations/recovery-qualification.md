# Recovery qualification

`make qualification-recovery` runs a destructive exercise only inside a newly
named disposable Compose project. It builds the backend and pinned signer
images, starts clean PostgreSQL and Redpanda volumes, and always attempts to
restore every stopped service before its scoped `down --volumes` cleanup. It
does not attach to or mutate the normal development project.

The exercise creates a fixture-backed reservation and sends the same valid
`create_signed_ticket` event twice. The production consumer must persist the
first event as settled, the transport replay as duplicate, and a following
malformed payload as quarantined. Exactly one charge must exist and the durable
PostgreSQL checkpoint must advance through all three records. The exercise then
stops and restores PostgreSQL, Redpanda, and the consumer individually and
rechecks those financial and quarantine invariants.

A separate topic is truncated before its first consumer assignment to produce
a controlled retention gap. Success requires a durable `retention_gap` record,
an open `transport_gap` reconciliation case, and an explicit checkpoint at the
first visible record. This is conservative reconciliation evidence; it does not
silently invent usage for the deleted records or create a charge.

## Signer boundary

The exercise invokes the unmodified pinned go-livepeer binary and runs the
root-owned signer wrapper validation lifecycle twice with disposable encrypted
keystore-envelope inputs. It also confirms all three public signer protocol
routes fail closed while no signer is financially ready. This validates image,
wrapper-restart, and routing boundaries without claiming a signed transaction.

A funded signer transaction remains an operator-only release gate. It needs a
real encrypted key, an on-chain RPC endpoint, gas, and TicketBroker
deposit and reserve. Use `make signer-preflight` and `make signer-smoke` with
operator-controlled inputs; neither this deterministic exercise nor a fake
chain result substitutes for that evidence.

## Evidence

The default output is `tmp/qualification/recovery.json`, or the path in
`QUALIFICATION_RECOVERY_EVIDENCE`. The bounded JSON records only service-state
outcomes, aggregate counts, offsets, image/binary identity, restoration, and
cleanup. It omits OTPs, credentials, signer secrets, and user identifiers.
Failure diagnostics are bounded and redacted by the shared qualification
harness.
