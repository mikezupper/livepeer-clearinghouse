# Architecture

Open Clearinghouse is a contract-first, hexagonal service that reserves
walletless spend before go-livepeer signs payment tickets and settles the
resulting usage asynchronously into an auditable ledger.

```text
browser ──> edge ──┬──> admin-web
                   ├──> user-web
                   ├──> clearinghouse API ─────────────> PostgreSQL
                   │         ▲                              ▲
                   │         │ authorize callback           │ atomic settlement
                   └──> go-livepeer remote-signer ──> Redpanda ──> consumer
```

This document describes the implemented reference distribution. The
[walking-slice specification](docs/product-specs/walking-slice.md) defines
product scope, and [core beliefs](docs/design-docs/core-beliefs.md) are the
normative invariants when the two appear to compete.

## System context

The clearinghouse sits between an application user and a Livepeer remote
signer. It is responsible for identity, policy, bounded exposure, usage
correlation, pricing, accounting, and the audit trail. It does not custody the
user's wallet or replace the Livepeer protocol.

Actors cross the system through distinct credentials:

- operators and tenant administrators use server-backed browser sessions;
- credential holders use browser sessions to create and manage scoped opaque
  API credentials and short-lived signer sessions;
- go-livepeer uses a deployment-owned webhook secret when asking the private
  authorization endpoint for a decision; and
- the metering consumer trusts a deployment-owned topic binding, not identity
  fields inside the Kafka payload.

Every boundary rejects unbounded or structurally invalid input before it enters
an application workflow.

## Deployable components

| Component | Responsibility |
| --- | --- |
| Edge | Same-origin Caddy ingress, security headers, web routing, API proxying, and the three exact go-livepeer protocol routes |
| Clearinghouse API | Authentication, onboarding, accounts, credentials, catalog, sessions, admission, metering reads, operations, and adapter composition |
| Metering consumer | Decode the dedicated signer topic and atomically normalize, correlate, rate, settle, quarantine, reconcile, and checkpoint |
| PostgreSQL | Durable authority for identity, policy, idempotency, audit, leases, receipts, usage, charges, operations evidence, and double-entry ledger facts |
| Redpanda | Kafka-compatible transport for go-livepeer monitoring events; never a financial source of truth |
| go-livepeer remote signer | Calculate payment state, ask the clearinghouse to authorize it, sign tickets, and emit `create_signed_ticket` |
| Admin web | Operator and tenant-administrator controls for tenants, accounts, principals, grants, pricing, catalog policy, invitations, emergency stops, adapters, and status |
| User web | Authentication, scoped credentials and signer sessions, catalog, usage, charges, balances, and profile state |
| Operations jobs | Guarded status, reconciliation, retention, backup, restore verification, rotation evidence, capacity inventory, and telemetry qualification |

The API and metering consumer are separate processes built from the same Python
package. Admin and user web are separate Vite applications that share Effect
contracts, platform services, Lit components, and global design tokens.

## Runtime topology and trust boundaries

The reference Compose deployment publishes only
`127.0.0.1:${CLEARINGHOUSE_EDGE_PORT:-8080}`. Caddy routes `/v1` and `/health`
to the API, `/admin/` to the admin application, `/` to the user application,
and `/generate-live-payment`, `/sign-orchestrator-info`, and
`/discover-orchestrators` to the signer. The go-livepeer admin listener remains
on container loopback and is not proxied.

```text
                         egress network
                    Resend/OAuth      chain RPC
                         ▲                ▲
                         │                │
host loopback -> edge -> API          remote-signer
                 │       │                 │
              app network│                 │
                         │                 │
                  database network   broker network
                         │                 │
                     PostgreSQL        Redpanda
                         ▲                 │
                         └──── consumer <─┘
```

The `database` and `broker` networks are Docker-internal. The edge cannot reach
either. Redpanda is not attached to the browser application network. API and
signer egress exists for their external provider/RPC responsibilities. Directly
publishing the API, database, broker, or signer admin port changes this threat
model and requires a new ingress and network review.

Browser traffic, email/OAuth responses, chain RPC responses, signer callbacks,
Kafka records, environment values, mounted files, and database rows are all
untrusted at their respective edges. Secret files are validated by hardened
entrypoints and read only from canonical mounts; file-indirection variables are
then removed before the process drops to UID/GID 10001. Application containers
use read-only roots, no-new-privileges, and empty capability bounding sets after
startup. See the [security model](docs/SECURITY.md) and
[threat model](docs/security/threat-model.md).

## Backend boundaries

Each domain follows this execution flow:

```text
domain types -> application ports -> workflows -> inbound/outbound adapters
```

Compile-time imports point inward: adapters depend on workflows and port
contracts; workflows depend on domain types and port contracts. Domain code
imports neither FastAPI, SQLAlchemy, Kafka, OAuth, Resend, nor vendor SDKs.
Adapters translate at the boundary and are wired once during startup.
Structural tests enforce the permitted dependency graph.

The adapter vocabulary covers identity, signer, custody, metering, pricing,
collection, and events-out. The implemented process manifest reports only the
ports actually wired: built-in identity, signer, metering, pricing, and
collection adapters as their backing services are composed. Custody is owned by
the remote-signer container, and there is no outbound event dispatcher in this
walking slice.

PostgreSQL is the reference distribution's fixed store. The running reference
image wires adapters explicitly in the composition root; it does not perform
annotation scanning, accept arbitrary dotted imports, or install code at
runtime. Trusted extension packages or HTTP bridge adapters belong in reviewed,
immutable derived deployments and must meet the versioned manifest and
conformance contract before they can be selected. See
[adapter loading](docs/design-docs/adapter-loading.md).

## Domain ownership and invariants

| Domain | Authoritative state and rules |
| --- | --- |
| Identity | Provider subject, principal, tenant/account scope, roles, invitations, browser sessions, and revocation |
| Accounts | Tenant/account lifecycle, opaque credentials, grants, rate cards, catalog policy, and balance projections |
| Signer | Signer sessions, leases, global/account caps, state heads, reservation receipts, and kill switch |
| Metering | Raw observations, quarantine, usage events, charges, reconciliation cases, worker heartbeat, and Kafka checkpoints |
| Operations | Audit events, operation jobs, projection checks, retention/legal holds, backup catalog, and adapter manifests |

Financial facts use integers or exact decimals with an explicit unit. A charge
names one immutable usage event and price snapshot; a usage event names one
reservation and lease; the lease records the balance and policy that authorized
it. Ledger transactions have at least two postings in one unit and sum to zero.

PostgreSQL constraints, row locks, advisory locks, and transactions protect
these relationships under concurrency. Derived account and global exposure
projections can be rebuilt and compared with their authoritative facts. Drift
does not become a silent correction: reconciliation records the selected
high-water mark and escalates invariant violations by stopping admission.

Opaque externally visible IDs keep database sequence or identity details out
of contracts. Credential, OTP, browser-session, signer-session, and client-IP
values are stored only as keyed hashes where equality is required. Plaintext
credentials and session tokens are returned once and cannot be reconstructed.

## Authentication and authorization

Email authentication is always available and uses a six-digit code delivered
through the configured Resend-compatible API. Google OIDC and GitHub OAuth are
enabled only by complete, validated configuration. Provider subjects—not email
address equality—anchor identity. OAuth uses authorization code flow, PKCE,
single-use state, a browser flow cookie, and a Google nonce.

Successful first-time authentication creates an active but unscoped principal.
It gains authority only through the explicit invitation/linking workflow or the
one-shot initial operator bootstrap. Linking revalidates issuer and target scope
inside locked database transactions and revokes the source sessions.

Browser sessions are opaque, server-backed, inactivity bounded, and absolutely
bounded. Mutations require both the `HttpOnly` session cookie and a separate
double-submit CSRF token plus an exact configured Origin. Reads and mutations
resolve authorization through a single actor dependency so tenant scope cannot
diverge between checks. See
[authentication design](docs/design-docs/authentication.md).

## Authorize and settle

1. A credential holder requests a signer session.
2. The core opens a capped, expiring lease inside a database transaction.
3. go-livepeer prepares a ticket batch and updated payment state, then calls the
   compatibility authorization endpoint immediately before signing that state.
4. The core reconstructs the supported typed request's amount, atomically moves
   it from lease availability to a pending reservation, and records a receipt
   keyed by signer state and sequence. It returns a stable opaque authorization
   identity with caching disabled.
5. After signing, go-livepeer emits one `create_signed_ticket` event for the
   request/batch to Redpanda.
6. The consumer validates and deduplicates the event, correlates its reservation
   receipt, and atomically commits usage, charge, lease settlement, ledger
   entries, and Kafka offset progression.
7. Sequence advancement identifies a missing prior Kafka confirmation; the
   reservation stays held and enters reconciliation rather than being silently
   released or charged twice.

The reservation identity is `(signer_id, state_id, sequence_number)`. The
Kafka envelope UUID deduplicates raw transport only; it is not the business
identity because go-livepeer creates it at send time. A canonical usage ID is
derived from the immutable reservation identity.

Authorization supports only payment shapes whose conservative reservation can
be reconstructed from the pinned callback contract. Untyped `inPixels`
requests are denied. The callback returns `expiry: 0` so go-livepeer cannot
cache an allow decision across payment calls.

The metering worker commits raw-event deduplication, usage, charge, ledger,
lease/receipt transition, and its next Kafka offset in one PostgreSQL
transaction. It seeks from that durable checkpoint on assignment. Malformed
events are quarantined with bounded metadata; an expired low watermark becomes
an explicit retention-gap record. Neither case can fabricate settlement or
restore pending value.

go-livepeer enqueues monitoring asynchronously and can lose a final event if
its queue fills or its Kafka retries fail. A later sequence can confirm that a
prior signed state escaped, but a missing final event remains an unresolved
conservative hold until an operator applies evidence-backed reconciliation.

See [core beliefs](docs/design-docs/core-beliefs.md) and
[remote-signer metering](docs/design-docs/remote-signer-metering.md) for
normative rules. See [adapter loading](docs/design-docs/adapter-loading.md) for
extension and deployment rules.

## Frontend boundary

The browser applications display and invoke the authoritative API; they do not
contain financial or authorization business logic. Untrusted API, URL, storage,
and event data is decoded through Effect Schema. Fallible workflows use typed
Effect error channels and capability layers; components do not call `fetch`
directly.

Lit components prefer native landmarks, headings, forms, lists, tables,
dialogs, buttons, links, and live regions. Each app has exactly one global
stylesheet for tokens, typography, themes, and page layout. Shadow component
CSS is restricted to encapsulated structure and consumes inherited properties;
components expose `part`/`exportparts` where application-level styling is
intentional. Global selectors cannot otherwise cross the Shadow DOM boundary.

Pure contracts and Effect workflows run under Vitest. Component semantics and
behavior run in real Chromium; critical journeys and accessibility run in
Playwright against production builds, with Firefox and WebKit smoke coverage.
See [frontend engineering](docs/FRONTEND.md).

## Startup and failure semantics

The reference startup order is dependency driven:

1. PostgreSQL and Redpanda become healthy.
2. `redpanda-init` verifies or creates the exact dedicated topic policy.
3. Alembic upgrades PostgreSQL to the locked migration head.
4. The optional one-shot operator bootstrap completes.
5. API, consumer, both web applications, and edge become healthy.
6. The default full stack starts the signer only after its volume initializer,
   API, and topic are ready and its operator-owned preflight has passed.

Liveness means a process can respond. API readiness additionally verifies
configuration, database revision/access, and required composed dependencies.
Consumer health requires a fresh durable heartbeat and a live broker loop. The
signer container health check proves only that its private protocol listener
accepts TCP; `make signer-smoke` owns chain, funding, webhook, broker, and admin
diagnostics.

Authorization, database, policy, and ledger failures deny distinctly and fail
closed. Kafka offsets advance only with the corresponding database transaction.
Duplicate delivery is harmless. Pending receipts never become spendable merely
because a dependency is unavailable.

Operational recovery is built around PostgreSQL authority: encrypted backups,
isolated restore verification, guarded schema downgrade, durable broker
checkpoints, explicit gap/poison reconciliation, kill-switch reopening, and
bounded telemetry. The single-node Compose topology demonstrates these
protocols but does not provide production high availability, point-in-time
recovery, or deployment capacity. See [reliability](docs/RELIABILITY.md) and the
[operations handbook](docs/operations/index.md).

## Contracts and evolution

`contracts/openapi.yaml` is the canonical HTTP contract and must match the
deterministically generated FastAPI schema. `contracts/asyncapi.yaml` and the
referenced JSON Schemas define the bounded Kafka envelope and canonical usage
event. Frontend Effect schemas and the custom-elements manifest are generated
or checked against these contracts.

Database changes are ordered Alembic migrations. The quality gates exercise a
fresh head-to-base-to-head cycle plus populated transitions and destructive
downgrade refusals. Release artifacts bind application version, contracts,
schema identity, image digests, SBOMs, checksums, provenance, and signatures.

## Explicitly outside the reference boundary

- OpenMeter, Stripe, Auth0, Turnkey, and Pymthouse commercial services.
- Runtime installation or discovery of untrusted adapter code.
- Automatic funding, key generation, or funded remote-signer transactions.
- A highly available database or Kafka cluster in the supplied Compose file.
- The optional `/v1/jobs` gateway and an outbound event dispatcher.
- Silent release of unresolved reservations or automatic repair without an
  audited operator decision.
- A go-livepeer patch; any upstream enhancement remains optional backlog work.
