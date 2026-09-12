# Livepeer Open Clearinghouse

[![Required quality](https://github.com/livepeer/clearinghouse/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/livepeer/clearinghouse/actions/workflows/ci.yml)
[![Security](https://github.com/livepeer/clearinghouse/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/livepeer/clearinghouse/actions/workflows/security.yml)
[![OpenSSF Scorecard](https://github.com/livepeer/clearinghouse/actions/workflows/scorecard.yml/badge.svg?branch=main)](https://github.com/livepeer/clearinghouse/actions/workflows/scorecard.yml)
[![Release](https://github.com/livepeer/clearinghouse/actions/workflows/release.yml/badge.svg)](https://github.com/livepeer/clearinghouse/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Open Clearinghouse is a small, extensible access and metering core for Livepeer workloads. A user signs in without a wallet, discovers runner capabilities and advertised prices, creates short-lived workload access for the official Python gateway SDK, and sees measured usage and cost. A configured administrator can inspect the system and stop new signer authorization globally.

The reference distribution is maintained by Livepeer at [`livepeer/clearinghouse`](https://github.com/livepeer/clearinghouse).

## Motivation

Applications using Livepeer should not need to embed Ethereum custody or rebuild identity, discovery, quoted access, and usage correlation. The core deliberately does only the shared protocol work:

1. authenticate a person and give them one personal account;
2. expose current Livepeer capabilities, constraints, orchestrators, and exact advertised prices;
3. bind a short-lived credential to one immutable workload quote;
4. authorize the unmodified go-livepeer remote signer against that quote; and
5. attribute `create_signed_ticket` events to the workload, user, and account.

The reported cost view keeps two facts distinct: the cost calculated from the observed quote and measured quantity, and the fee reported by the signer. This makes discrepancies visible without turning the core into a ledger or billing platform.

OpenMeter, Stripe, enterprise tenancy/RBAC, grants, double-entry accounting, retention automation, and support tooling are intentionally outside the core. They can be layered on through versioned HTTP/event contracts and typed ports without forking domain behavior.

## Included functionality

| Area | Implementation |
| --- | --- |
| Identity | Resend-compatible six-digit email OTP; optional Google OIDC and GitHub OAuth |
| Accounts | One direct personal account per authenticated user; one configured admin email |
| Discovery | Livepeer runner capability/model/constraint discovery with exact rational prices |
| SDK access | Revocable account API credentials and short-lived per-workload SDK tokens |
| Signer policy | Quote, expiry, orchestrator, state-binding, credential, and global-stop checks |
| Metering | Redpanda ingestion of pinned go-livepeer `create_signed_ticket` events |
| Storage | SQLite with WAL, foreign keys, a busy timeout, and one serialized writer boundary |
| Web | Separate Lit/Effect user and admin apps, served together by Caddy |
| Runtime | Four long-running services: edge, core, Redpanda, and remote signer |

## Architecture

```text
browser / Python gateway SDK
             │
             ▼
      Caddy edge (:8080)
       │       │       └──────── signer protocol ───────┐
       │       └── /admin ── admin Lit app              │
       ├── / ─────────────── user Lit app               ▼
       └── /v1, /health ── Python core ◀── webhook ─ go-livepeer
                                │                         │
                                ├── SQLite               │
                                └── Kafka consumer ◀─ Redpanda
```

The API and Kafka consumer run in one supervised Python process so SQLite has a controlled writer boundary. The edge image contains both independently built web applications. The broker and SQLite database are not published to the host. The remote signer is the unmodified pinned upstream image; its custody files remain operator-owned.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and the [simplified core design](docs/design-docs/simple-core.md) before changing a boundary.

## Quick start

Prerequisites: Git, Docker Engine with Compose, Make, and UV 0.12.12 or newer.

```sh
git clone https://github.com/livepeer/clearinghouse.git
cd clearinghouse
make init-env
```

Edit `.env`, supply the signer custody files described below, then:

```sh
make signer-preflight
make up
make ps
```

Open `http://localhost:8080/` for users and `http://localhost:8080/admin/` for administration. The development defaults also trust `http://127.0.0.1:8080` for browser mutations. `make down` preserves SQLite, signer, and Redpanda volumes. `make destroy DESTROY=1` explicitly removes them.

## Configuration

`.env.example` is the complete inventory. `.env` is ignored and must never be committed.

### Core and authentication

| Variable | Meaning |
| --- | --- |
| `CLEARINGHOUSE_ENVIRONMENT` | `development`, `test`, or `production` |
| `CLEARINGHOUSE_PUBLIC_URL` | Browser-facing edge base URL |
| `CLEARINGHOUSE_ALLOWED_ORIGINS` | Comma-separated exact origins allowed for browser mutations |
| `CLEARINGHOUSE_COOKIE_SECURE` | Must be `true` with HTTPS in production |
| `CLEARINGHOUSE_ADMIN_EMAIL` | The email whose personal account receives admin access |
| `CLEARINGHOUSE_AUTH_PEPPER` | Independent keyed-hash secret for OTPs and browser/API credentials |
| `CLEARINGHOUSE_WORKLOAD_PEPPER` | Independent keyed-hash secret for workload access tokens |
| `CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET` | Shared private credential for the signer callback |

Production requires HTTPS, secure cookies, and at least 32-character non-placeholder peppers/secrets.

### Email OTP and optional OAuth

| Variable | Meaning |
| --- | --- |
| `CLEARINGHOUSE_AUTH_RESEND_API_URL` | Official Resend base URL or custom compatible URL |
| `CLEARINGHOUSE_AUTH_RESEND_API_KEY` | API key sent by the official Resend SDK |
| `CLEARINGHOUSE_AUTH_RESEND_FROM` | Verified sender identity |
| `CLEARINGHOUSE_AUTH_GOOGLE_ENABLED` | Enables Google only with both client values |
| `CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_ID` | Optional Google client ID |
| `CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_SECRET` | Optional Google client secret |
| `CLEARINGHOUSE_AUTH_GITHUB_ENABLED` | Enables GitHub only with both client values |
| `CLEARINGHOUSE_AUTH_GITHUB_CLIENT_ID` | Optional GitHub client ID |
| `CLEARINGHOUSE_AUTH_GITHUB_CLIENT_SECRET` | Optional GitHub client secret |

Email OTP is always available. Google and GitHub do not appear in the UI unless explicitly enabled with complete configuration. There is no invitation code: a successful email/OAuth sign-in creates or resolves the user's personal account. Admin access is determined solely by `CLEARINGHOUSE_ADMIN_EMAIL` at sign-in.

### Discovery and static orchestrators

`CLEARINGHOUSE_DISCOVERY_URLS` names one or more comma-separated priced discovery endpoints. The Clearinghouse accepts only runner-and-price records from that boundary; it never bypasses signer filtering by querying orchestrators itself. A successful partial refresh replaces offers only for the orchestrators it reports and retains other orchestrators' last valid observations until their TTL. If the whole refresh is incomplete, the last safe snapshot remains usable for at most `CLEARINGHOUSE_DISCOVERY_TTL_SECONDS` (one hour by default). Any response containing retained observations is identified by `X-Clearinghouse-Discovery-Stale: true` and carries an HTTP `Warning: 110` header. With no safe snapshot, discovery returns `503`. Every generated Python SDK token pins the orchestrator service address from its selected offer.

Collection APIs use opaque cursor pagination: 50 items by default, at most 200,
with `next_cursor` continuation and no offset or embedded total count. The user
and administrator overview endpoints use bounded SQL summaries. Offer cursors
are tied to the discovery generation and return an explicit `409 stale_cursor`
when the network snapshot changes. See the
[pagination contract](contracts/http/v1/pagination.md) for endpoint behavior,
stable ordering, and the Python gateway compatibility exception.

For the bundled go-livepeer signer, keep `SIGNER_REMOTE_DISCOVERY=true`. To publish a fixed list, set both:

```dotenv
SIGNER_REMOTE_DISCOVERY=true
SIGNER_ORCH_ADDR=https://orch-a.example:8935,https://orch-b.example:8935
```

`SIGNER_ORCH_ADDR` values are orchestrator service addresses, not Ethereum addresses. The pinned go-livepeer flag requires remote discovery to remain enabled when this list is supplied.

### Signer and broker

| Variable | Suggested evaluation value |
| --- | --- |
| `SIGNER_MODE` | `evaluation` |
| `SIGNER_NETWORK` | `arbitrum-one-mainnet` |
| `SIGNER_CHAIN_ID` | `42161` |
| `SIGNER_CONTROLLER` | `0xD8E8328501E9645d16Cf49539efC04f734606ee4` |
| `SIGNER_MIN_GAS_WEI` | `1000000000000000` (0.001 ETH) |
| `SIGNER_MIN_DEPOSIT_WEI` | `1` |
| `SIGNER_MIN_RESERVE_WEI` | `1` |
| `LP_KAFKAUSER` / `LP_KAFKAPASSWORD` | blank for the private local broker |

Set `ETH_RPC_URL`, `SIGNER_ETH_ADDR`, `SIGNER_KEYSTORE_HOST_FILE`, and `SIGNER_PASSWORD_HOST_FILE` to real operator-managed values. The key file must be encrypted Ethereum V3 JSON outside the repository, and the password file must not be world-accessible. The current pinned go-livepeer process may include the complete RPC URL in its logs; use a non-credential URL where possible and treat signer logs accordingly. Redacting `ethUrl` upstream remains optional follow-up work.

## User workflow and Python SDK

1. Sign in by email code.
2. Open Network to inspect advertised offers and exact rates.
3. Open Cost estimator, select an offer, and enter the assumptions requested for its billing unit.
4. Review the estimated quantity, cost, and offer expiry, then create a workload using an optional client/job reference.
5. Save the one-time Python SDK token.
6. Pass it as the `LIVEPEER_GATEWAY_ACCESS_TOKEN` expected by [`livepeer-python-gateway`](https://github.com/livepeer/livepeer-python-gateway), or decode the documented payload to configure signer/discovery access directly.
7. Inspect Usage & cost for measured quantity, quote-derived cost, and signer fee.

The exact compatibility contract used by the local SDK checkout is documented in [contracts/sdk/livepeer-python-gateway-v1.md](contracts/sdk/livepeer-python-gateway-v1.md).

### Ad-hoc Python gateway qualification

Live orchestrator qualification is intentionally a developer operation rather
than a CI gate: availability, capabilities, prices, chain state, and metering
latency are external inputs. Copy `qualification.env.example` to the ignored
`qualification.env`, restrict it to mode `0600`, and set an account API
credential. Versioned case definitions live in
[`config/qualification/cases.v1.json`](config/qualification/cases.v1.json). The
suite defaults to an aggregate ceiling of `1000000000000` wei, a per-case
ceiling of `100000000000` wei, a 30-second duration ceiling, and disabled live
LV2V execution. Start with the read-only planner:

```sh
make qualify-billing-plan
```

The plan labels every case `runnable`, `unavailable`, `ambiguous`, or
`over-budget` before creating a workload. Controlled cases exercise the real
SQLite store, signer policy, event decoder, idempotency constraints, and cost
aggregation without network spend:

```sh
make qualify-billing-controlled
make qualify-billing-broker
```

Live execution requires both `QUAL_EXECUTE=true` and an explicit case list:

```sh
QUAL_EXECUTE=true make qualify-billing-run CASES=persistent-short
QUAL_EXECUTE=true make qualify-billing-run CASES=persistent-short,persistent-multi-cycle
make qualify-billing-report
```

The broker target replays qualification-owned duplicate and delayed events
through the running Redpanda and core consumer; it signs no tickets and spends
zero wei. The cases cover fixed-price success/paid failure/pre-payment rejection,
single- and multi-cycle time metering, interrupted workloads, exact rational
runtime repricing, duplicate/delayed/foreign signer events, and LV2V pixel
accounting. Live fixed execution additionally requires
`QUAL_FIXED_PAYLOAD_JSON`; live LV2V requires an authoritative priced offer,
explicit media/model inputs, and `QUALIFICATION_ALLOW_LV2V=true`. Missing live
inventory is reported as not runnable, never as a pass.

Sanitized plans, per-case evidence, and `report-latest.md` are written beneath
`tmp/qualification/`; credentials and workload tokens are never included.
Executed workloads are revoked after evidence is collected. The operation
fails closed on stale discovery unless `QUAL_ALLOW_STALE_DISCOVERY=true` is
explicitly staged. Quote-derived cost is required to reconcile exactly with
measured quantity. The signer-computed fee is reported separately because
ticket and funding granularity can make it larger than the advertised
usage-derived amount.

See [the billing qualification runbook](docs/operations/billing-qualification.md)
for the complete case matrix and evidence interpretation.

### Authoritative LV2V prices

Remote-signer runner discovery is authoritative for priced `runners[]`, but it
does not provide the traditional GetOrchestrator `capabilities_prices` boundary
needed to quote LV2V. Operators may create ignored
`config/lv2v-offers.json` from
[`config/lv2v-offers.example.json`](config/lv2v-offers.example.json), verify its
exact on-network price and orchestrator address, and set:

```dotenv
CLEARINGHOUSE_LV2V_OFFERS_FILE=/app/config/lv2v-offers.json
```

The Compose service mounts `./config` read-only. An incomplete record, a unit
other than `720p-pixel-seconds`, a non-wei currency, or an unavailable file
invalidates the complete refresh and preserves the previous safe snapshot.

## Storage and enterprise extension

SQLite is the bundled durable authority and is appropriate for the documented single-node profile. The core uses portable entities and the `CoreStore`/`CoreTransaction` protocol. An enterprise PostgreSQL adapter is a separately packaged composition-root choice: it must pass the same storage conformance suite and preserve IDs, exact-price semantics, transactions, and uniqueness. No runtime annotation scanning or arbitrary module import is used.

Extension points are versioned under `contracts/ports/v2`; public HTTP behavior is in `contracts/openapi.yaml`, and signer events are in `contracts/asyncapi.yaml`. Enterprise services should consume these boundaries rather than import SQLite internals.

## Frontend conventions

Both apps use Lit web components, Effect, TypeScript, Vite, and Vitest. Markup uses native semantic elements. No inline styles or utility-class framework is allowed. Shared global CSS owns the zinc/emerald visual language, themes, typography, and tokens. Shadow DOM components consume inherited custom properties and expose/forward stable Shadow Parts so global CSS retains deliberate control; component CSS is limited to encapsulated structure.

See [docs/FRONTEND.md](docs/FRONTEND.md).

## Build and quality

```sh
uv sync --frozen
cd frontend && npm ci --ignore-scripts && cd ..
make test-backend
make test-frontend
make test-browser
make build
```

`make test` runs the complete local gate. Coverage is enforced independently at a minimum of 85% for lines, statements, functions, and branches for the Python backend and each frontend codebase. Browser validation covers Chromium journeys and accessibility, responsive visual regression, and Firefox/WebKit smoke tests.

Docker images are built through `make build`:

- `livepeer/clearinghouse-core:local`
- `livepeer/clearinghouse-edge:local`
- `livepeer/clearinghouse-remote-signer:local`

## Operations and limitations

- SQLite and Redpanda are single-node components. Back up the SQLite volume and operate a replicated Kafka deployment before claiming multi-node availability.
- The core fails authorization closed on an invalid token, expired/revoked workload, price increase, orchestrator mismatch, state rebinding, or global stop.
- Kafka events are idempotent by signer plus transport event ID. Unknown `auth_id` events remain visible as unmatched evidence.
- Discovery prices are observations, not promises; workload creation freezes the selected observation.
- This core measures and attributes costs. It does not collect payment, maintain balances, or settle invoices.

Deployment details are in [docs/operations/deployment.md](docs/operations/deployment.md), signer controls in [docs/operations/signer.md](docs/operations/signer.md), and security reporting in [SECURITY.md](SECURITY.md).

## Project governance

Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and [GOVERNANCE.md](GOVERNANCE.md). Work is tracked in Beads. The project is licensed under the [MIT License](LICENSE), copyright Livepeer.
