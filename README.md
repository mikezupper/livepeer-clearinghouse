# Livepeer Open Clearinghouse

[![Required quality](https://github.com/livepeer/clearinghouse/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/livepeer/clearinghouse/actions/workflows/ci.yml)
[![Security](https://github.com/livepeer/clearinghouse/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/livepeer/clearinghouse/actions/workflows/security.yml)
[![OpenSSF Scorecard](https://github.com/livepeer/clearinghouse/actions/workflows/scorecard.yml/badge.svg?branch=main)](https://github.com/livepeer/clearinghouse/actions/workflows/scorecard.yml)
[![Release](https://github.com/livepeer/clearinghouse/actions/workflows/release.yml/badge.svg)](https://github.com/livepeer/clearinghouse/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Open Clearinghouse is a vendor-neutral service for walletless spend on the
Livepeer network. It lets an operator grant exact-value credit, lets an
application obtain a bounded signer session without holding an Ethereum key,
and turns confirmed remote-signer usage into reproducible charges and a
double-entry balance.

The reference distribution is a production-oriented walking slice: a Python
API and metering worker, PostgreSQL, a Kafka-compatible Redpanda broker, an
unmodified pinned go-livepeer remote signer, and separate Lit applications for
operators and account users. It is maintained by Livepeer at
[`livepeer/clearinghouse`](https://github.com/livepeer/clearinghouse).

## Why this exists

Livepeer applications should not each have to build wallet custody, admission
control, identity, usage correlation, pricing, and accounting before they can
offer a walletless experience. Open Clearinghouse separates those concerns
from a product's commercial stack and makes the financial path explicit:

1. an operator posts an audited grant to an account;
2. a credential holder opens a capped, expiring lease;
3. the clearinghouse reserves spend immediately before go-livepeer signs;
4. go-livepeer publishes its normal `create_signed_ticket` event; and
5. the metering worker settles one usage event, one charge, and balanced ledger
   postings in a single database transaction.

The service is intentionally not a billing platform or hosted control plane.
OpenMeter, Stripe, Auth0, Turnkey, Pymthouse, marketplace, merchant-resale, and
support-console integrations are outside this walking slice. Pymthouse can be
integrated later through trusted adapters; it is not a core dependency.

## What is included

| Component | Reference implementation |
| --- | --- |
| API and metering worker | Python 3.14, FastAPI, SQLAlchemy, Alembic, and UV |
| Durable authority | PostgreSQL 18 for identity, policy, leases, receipts, usage, charges, ledger, audit, and checkpoints |
| Signer event transport | Single-node Redpanda with a dedicated, one-partition metering topic |
| Remote signer | Unmodified, commit-pinned upstream go-livepeer behind a hardened wrapper |
| Admin application | Lit, Effect, TypeScript, Vite, and real-browser tests at `/admin/` |
| User application | Lit, Effect, TypeScript, Vite, and real-browser tests at `/` |
| Public edge | Caddy, with only a loopback port published by the reference Compose stack |

The admin application manages tenants, accounts, principals, grants, rate
cards, catalog policy, invitations, the kill switch, adapters, and operational
status. The user application manages credentials and signer sessions and shows
the account catalog, balance, usage, and charges. Both use native semantic HTML
inside Shadow DOM. Global CSS owns design tokens, typography, themes, and
application layout; components consume inherited custom properties and expose
Shadow Parts for intentional global control. Inline styles are prohibited.

See the [walking-slice specification](docs/product-specs/walking-slice.md) for
the supported journeys and explicit non-goals.

## Architecture at a glance

```text
browser ──> loopback/HTTPS edge ──> user web or admin web
                   │
                   ├── /v1, /health ──> Python API ──> PostgreSQL
                   │                                    ▲
                   └── signer protocol ─> go-livepeer   │ atomic settlement
                                             │          │
                                             └──────> Redpanda ──> metering worker
```

PostgreSQL is the financial and authorization authority. Redpanda transports
signer evidence; it is not a balance store. Browser, email/OAuth providers,
chain RPC, signer callback, and Kafka payloads are separate trust boundaries.
The database and broker are on private internal networks and are not published
to the host.

The backend uses inward-only dependencies:

```text
domain types <- application workflows and typed ports <- adapters/infrastructure
```

Untrusted HTTP, Kafka, environment, and database data is decoded at its edge.
Money and quantities use integers or exact decimals, never binary floating
point. Authorization and ledger failures fail closed, and writes use bounded
idempotency keys and database serialization. Adapters are selected, validated,
and wired explicitly at process startup; the reference image does not scan for
annotations or load arbitrary modules from environment variables.

Read [ARCHITECTURE.md](ARCHITECTURE.md), the
[core invariants](docs/design-docs/core-beliefs.md), and the
[adapter model](docs/design-docs/adapter-loading.md) before changing a boundary.

## Five-minute local start

You need Git, GNU Make or compatible `make`, Docker Engine, the Docker Compose
plugin, and UV 0.12.12 or newer. UV provisions the pinned Python interpreter
used by deployment preflight; the Docker build supplies the service and Node
toolchains, so no host Node install is needed for this first check.

```sh
git clone https://github.com/livepeer/clearinghouse.git
cd clearinghouse
make init-env
make smoke
```

`make init-env` creates a mode-0600 `.env` from `.env.example`.
`make smoke` generates local-only development secrets, builds and starts the
keyless core services, and proves edge routing, API readiness, authorization
denial, PostgreSQL state, Redpanda delivery, normalization, quarantine, and
durable consumer offsets with a synthetic nonfinancial event.

After it passes, open `http://127.0.0.1:8080/` for the user application and
`http://127.0.0.1:8080/admin/` for the admin application. Email sign-in will
work only after `.env` points to a usable Resend or Resend-compatible endpoint;
the generated development API key is deliberately not a delivery credential.

Useful lifecycle commands are:

```sh
make ps       # show service health
make logs     # follow bounded container output
make down     # stop containers and preserve named volumes
make destroy DESTROY=1  # explicitly delete this Compose project's volumes
```

Run `make help` for the complete supported command surface. Do not use
`DESTROY=1` against data you intend to keep.

## Authentication and onboarding

Email one-time codes delivered through the official Resend Python SDK are the
default sign-in method. Configure:

| Setting | Purpose |
| --- | --- |
| `CLEARINGHOUSE_AUTH_RESEND_API_URL` | Resend API base URL or an HTTPS-compatible custom endpoint |
| `CLEARINGHOUSE_AUTH_RESEND_API_KEY_HOST_FILE` | Host file containing the API key |
| `CLEARINGHOUSE_AUTH_RESEND_FROM` | Verified sender address |
| `CLEARINGHOUSE_AUTH_PEPPER_HOST_FILE` | Independent keyed-hash secret for auth data |
| `CLEARINGHOUSE_AUTH_ALLOWED_ORIGINS` | Comma-separated exact browser origins |
| `CLEARINGHOUSE_AUTH_SUCCESS_REDIRECT_URL` | Post-authentication browser destination |

The code is six digits, short lived, attempt limited, and stored only as a
keyed hash. Google OIDC and GitHub OAuth are optional. A provider appears only
when its `*_ENABLED` value is true and its client ID, secret host file, and
redirect URI are all configured. Partial provider configuration prevents
startup. OAuth uses authorization code flow, PKCE, one-shot state, and—in the
Google flow—an OIDC nonce.

Google uses `CLEARINGHOUSE_AUTH_GOOGLE_ENABLED`,
`CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_ID`,
`CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_SECRET_HOST_FILE`, and
`CLEARINGHOUSE_AUTH_GOOGLE_REDIRECT_URI`. GitHub uses the corresponding
`CLEARINGHOUSE_AUTH_GITHUB_*` names shown in `.env.example`.

A newly verified identity is active but unscoped and has no tenant or operator
permissions. An operator or authorized tenant administrator must issue an
invitation that links it to an existing scoped principal. A first deployment
can instead provide the paired one-shot
`CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL_HOST_FILE` and
`CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET_HOST_FILE`; remove both after a
successful bootstrap. Browser mutations require the `och_session` cookie,
exact Origin, and matching `X-CSRF-Token`/`och_csrf` value.

See [authentication design](docs/design-docs/authentication.md) and the
[security model](docs/SECURITY.md) for the complete contract.

## Configuration

`.env.example` is the canonical configuration inventory. Copy it with
`make init-env`, keep `.env` untracked, and replace every marked value before a
real deployment. Configuration is parsed once and invalid or incomplete
production settings fail startup.

| Area | Principal settings |
| --- | --- |
| Runtime/database | `CLEARINGHOUSE_ENVIRONMENT`, `CLEARINGHOUSE_LOG_LEVEL`, `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD_HOST_FILE`, pool size and timeout |
| Browser/auth | Resend settings, OTP/rate limits, session lifetimes, secure-cookie flag, allowed origins, success redirect, optional OAuth settings |
| Bootstrap/credentials | Auth and credential pepper host files, invitation lifetime, optional paired bootstrap files |
| Signer/admission | Webhook secret and session pepper files, global exposure cap, signer ID and browser-facing signer/discovery URLs |
| Metering | Internal broker address, topic, consumer group/client IDs, payload bound, reconciliation interval/batch size, confirmation grace, heartbeat thresholds |
| Real signer | Mode, network, chain/controller, credential-free RPC URL, address, encrypted V3 keystore/password files, funding floors, internal webhook URL, Kafka credentials |
| Recovery/operations | Age recipient/identity, encrypted backup volume, backup key ID, and operator actor ID |
| Telemetry | OTLP endpoint and export interval |

All application, PostgreSQL, OAuth, signer, and backup secrets are mounted from
host files. Development helpers create only ignored mode-0600 non-custody
secrets under `tmp/runtime-secrets`. Production secret files must be managed
outside the checkout and Docker build context. The signer key and password are
never generated by the repository.

Production uses `CLEARINGHOUSE_ENVIRONMENT=production`, HTTPS URLs, secure
cookies, a deliverable sender, strong independent secrets, an exact public
origin list, and operator-managed custody files. Run:

```sh
make deployment-preflight
make operations-ownership-check \
  OPERATIONS_OWNERSHIP_FILE=/absolute/path/operations-ownership.env
```

The [reference deployment guide](docs/operations/deployment.md) explains secret
ownership, process privileges, network boundaries, image selection, and
production differences.

## Running the real remote signer

The default Compose model includes the remote signer; it is not hidden behind
an optional profile. Because the accepted upstream binary has no safe keyless
mode, `make up` refuses to start it without all of the following:

- an operator-owned encrypted Ethereum V3 keystore and separate password file;
- the matching signer address, selected chain and controller;
- a credential-free RPC URL;
- configured minimum gas, TicketBroker deposit, and reserve; and
- reachable clearinghouse webhook and dedicated Kafka topic.

Configure the signer section of `.env`, then run:

```sh
make signer-preflight
make up
make signer-smoke
```

`make signer-preflight` performs static, key/address, RPC, network, and funding
checks without logging secret material. `make signer-smoke` starts the stack and
runs read-only network-namespace diagnostics. A funded signing request is a
separate deliberate operator action; automated release qualification does not
claim to perform it. Never publish or proxy the signer's loopback-only admin
listener.

See [remote-signer operations](docs/operations/signer.md).

## Metering and accounting guarantees

No go-livepeer patch is required. The clearinghouse correlates two observations
from the pinned unmodified signer:

1. the authenticated authorization callback creates a conservative pending
   reservation before signing; and
2. a `create_signed_ticket` event normally confirms the signed request and its
   computed fee through the dedicated Redpanda topic.

The consumer commits deduplication, validated usage, a charge, balanced ledger
postings, reservation settlement, and its next Kafka offset atomically. A
duplicate cannot create another charge. A fork, poison event, fee above the
reservation, or expired broker offset is quarantined or becomes explicit
reconciliation state; it does not silently release pending exposure.

Kafka delivery is asynchronous inside go-livepeer and can be lost after queue
saturation or exhausted retries. A later signer sequence proves the prior
signed state escaped, but a lost final event can remain an unresolved
conservative hold. Operators must alert on producer errors, consumer lag,
sequence gaps, poison events, and aging reservations. The exact supported
payment shapes and residual risks are documented in
[remote-signer metering](docs/design-docs/remote-signer-metering.md).

The included Redpanda node has replication factor one and development settings.
Production needs an independently operated authenticated, authorized,
replicated broker with monitoring and tested recovery while preserving a
dedicated one-signer topic binding.

## Build and test

The host development toolchain is Python 3.14.7 with UV 0.12.12 or newer, and
Node 24 or newer with npm 12.0.2. Locked runtime and frontend dependencies live
in `uv.lock` and `frontend/package-lock.json`.

```sh
uv sync --frozen
cd frontend && npm ci --ignore-scripts && cd ..
make build
make test
```

`make test` is the complete local gate. It validates Compose and shell entry
points; formats, lints, and type-checks Python and TypeScript; checks repository
architecture and generated OpenAPI/AsyncAPI/custom-element artifacts; runs
unit, contract, real PostgreSQL/Redpanda, migration, Chromium journey,
accessibility, Firefox, and WebKit tests; and builds both web applications.

Coverage is enforced independently—not as a blended repository number—at a
minimum of 85% for lines, statements, functions, and branches for the backend,
admin app, user app, and each shared frontend package. Focused Make targets are
listed in [the quality policy](docs/QUALITY.md).

## Release qualification

The disposable qualification targets build production images under unique
Compose project names, use private generated secrets, write bounded sanitized
evidence under ignored `tmp/qualification/`, and clean up only their labeled
resources:

```sh
make qualification-harness
make qualification-journey
make qualification-recovery
make qualification-evidence
```

The aggregate proves the OTP-to-charge journey, both production web images,
tenant isolation, idempotency, kill-switch behavior, restart durability,
poison quarantine, explicit retention-gap reconciliation, migration cycles,
and an encrypted backup restored into an isolated PostgreSQL volume. It also
records exact component, image, contract, schema, and tool identities.

Its 32-request sequential readiness probe is a small reference-fixture health
gate, not a production capacity result. The reference stack is not highly
available, the backup exercise is not point-in-time recovery, and automated
qualification is not a funded signer transaction. Retain both the JSON and
Markdown manifests described in the
[qualification evidence runbook](docs/operations/qualification-evidence.md).

## Deployment, migrations, and recovery

`compose.yaml` is a hardened, reproducible reference topology, not a complete
production platform. Before serving traffic, an operator must provide TLS,
external secret management, immutable image digests, highly available
PostgreSQL and Kafka, backup/WAL infrastructure, production telemetry and
alerts, deployment-specific capacity evidence, incident ownership, and a
tested rollback procedure.

Alembic migrations run before API startup. Inspect the live revision with
`make migration-status`. Prefer a forward fix once new code may have written
the target schema. A downgrade is accepted only after writers stop and the
guarded target receives an exact prior revision, cataloged restore-verified
backup ID, and external change-record ID:

```sh
make migration-downgrade CONFIRM=migration-downgrade \
  REVISION=<prior_revision> VERIFIED_BACKUP_ID=<catalog_artifact_id> \
  CHANGE_RECORD_ID=<external_change_record_id> \
  OPERATIONS_OWNERSHIP_FILE=/absolute/path/operations-ownership.env
```

Encrypted logical backups and isolated restore verification are guarded too:

```sh
make backup CONFIRM=backup REASON='scheduled verification' \
  IDEMPOTENCY_KEY=backup-YYYYMMDDTHHMMSSZ

make restore-verify CONFIRM=isolated-restore BACKUP_NAME=<basename> \
  REASON='scheduled restore verification' \
  IDEMPOTENCY_KEY=restore-YYYYMMDDTHHMMSSZ
```

Never restore over the source database. Logical dumps do not meet the stated
five-minute production recovery-point objective by themselves; production
must add and exercise encrypted base backups and continuous WAL archiving.

Start with the [operations handbook](docs/operations/index.md) for service
levels, observability, reconciliation, retention, rotation, incident response,
broker recovery, backup/restore, migration rollback, capacity, and kill-switch
procedures.

## Releases and compatibility

The distribution follows Semantic Versioning, while HTTP, event, and adapter
contracts keep their own explicit compatibility versions. A release is one
coordinated set of six digest-addressed OCI images plus contracts, checksums,
SBOMs, provenance, and signatures. Read the
[release and compatibility policy](docs/RELEASING.md) before changing a public
contract, migration, configuration boundary, or release version. User-visible
changes are recorded in the [changelog](CHANGELOG.md).

## Repository conventions

- Beads (`bd`) is the only work tracker; do not add TODO/plan files or duplicate
  project work into GitHub issues.
- Parse untrusted data once at the boundary and keep domain dependencies
  pointing inward.
- Use exact integer/decimal financial representations and immutable audit facts.
- Keep Pymthouse and commercial services in optional adapters, never core.
- Use Lit and Effect for frontend workflows, semantic native HTML first, no
  inline `style`, global tokens/layout, and intentional Shadow Parts.
- Add tests with every behavior change and keep every independent coverage
  metric at or above 85%.
- Use Make targets as the supported local and CI command surface.

See [AGENTS.md](AGENTS.md), the [documentation index](docs/index.md), and
[work-tracking policy](docs/WORK_TRACKING.md) for the complete contributor map.

## Current limitations

- The Compose database and broker are single-node and not highly available.
- Automated qualification does not fund or submit a real signer transaction.
- A missing final signer Kafka event can require operator reconciliation while
  its reserved value remains unavailable.
- The initial signer admission policy rejects untyped `inPixels` requests
  because the callback cannot reconstruct them safely.
- The reference runtime wires built-in adapters; arbitrary runtime code loading
  is intentionally unavailable.
- The reference logical-backup flow does not provide continuous WAL archiving
  or a production RPO/RTO guarantee.
- The optional `/v1/jobs` gateway, commercial billing/custody providers, and a
  go-livepeer enhancement are future work, not scaffold requirements.

## License

Open Clearinghouse is available under the MIT License. Copyright Livepeer.
