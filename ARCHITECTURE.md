# Architecture

Open Clearinghouse is a contract-first, hexagonal core for Livepeer identity, discovery, quoted workload authorization, and signer-event metering.

## Runtime topology

The default Compose deployment has exactly four long-running services.

| Service | Responsibility |
| --- | --- |
| `edge` | Caddy security headers, same-origin API proxy, signer protocol proxy, and static delivery of both web apps |
| `core` | FastAPI, email/OAuth access, discovery, signer policy, SQLite persistence, and supervised Kafka consumption |
| `redpanda` | Private Kafka-compatible transport for signer monitoring events |
| `remote-signer` | Unmodified pinned go-livepeer signer and discovery endpoint |

```text
external clients ──> edge ──> core ──> SQLite volume
                       │         ▲
                       │         └── Redpanda <── remote-signer
                       └────────────────────────> remote-signer protocol
```

Only edge port 8080 is published, bound to loopback by default. Redpanda and core communicate on a private Compose network. The signer alone receives the Ethereum RPC URL and encrypted custody mounts.

## Dependency direction

```text
domain values <- application workflows + ports <- HTTP/Kafka/SQLite/provider adapters
```

The domain has no FastAPI, SQLite, OAuth, Resend, or Kafka dependency. Application services depend on `CoreStore`, discovery, email, and OAuth protocols. The composition root explicitly chooses audited adapters; it never scans annotations or imports an environment-named module.

## Core model

- A user resolves from a normalized verified email.
- Each user directly owns one personal account.
- An account API credential belongs to one user/account, authorizes control-plane requests, and is revocable.
- A capability offer records an orchestrator/runner observation and exact rational price.
- A workload freezes one offer, optionally freezes a maximum spend, and creates
  one short-lived, hashed access token.
- The first accepted signer state binds that workload to one state/orchestrator;
  each sequence can reserve exposure before tickets leave the signer.
- A usage event records signer evidence and is matched through `auth_id == workload_id`.
- A workload cost aggregates measured quantity, quote-derived fee, and signer-reported fee without treating either as payment settlement.

Opaque secret values are returned once and persisted only as keyed digests. Prices and fees use integers and exact ratios, never binary floating point.

## SQLite boundary

The default database enables foreign keys and WAL, applies a bounded busy timeout, and serializes writers through the one core process. Schema initialization is idempotent and transactional. The named `sqlite-data` volume survives core container replacement.

`CoreStore` and `CoreTransaction` are the storage compatibility boundary. A separately distributed PostgreSQL adapter can replace SQLite at composition time after passing the conformance suite. The core does not carry an unused PostgreSQL dependency or dialect-specific behavior.

## Authentication boundary

Email OTP is the default. Challenges have six digits, a bounded lifetime and attempts, and only a keyed digest is stored. Successful verification resolves the personal account and issues an HTTP-only same-site browser session plus a separate CSRF value. Browser mutations require a configured exact Origin and matching CSRF cookie/header. Account API credentials bypass browser CSRF and remain account-scoped; they are not signer credentials.

Google and GitHub are optional. Startup rejects enabled or partial provider configuration unless both client ID and secret are present. OAuth uses authorization code, PKCE, one-shot state, and Google nonce verification.

Admin access is a property resolved from the configured admin email. There are no roles, invitations, tenants, or bootstrap operators in the core.

## Discovery and quote boundary

The discovery adapter normalizes go-livepeer/live-runner responses into capability offers containing runner URL, optional orchestrator address, capability, model, constraints, exact price, observation time, and expiry. Workload creation accepts only a current offer and copies its price. Later discovery changes do not mutate an existing workload quote.

Static orchestrators are explicit deployment inputs. They are embedded in generated SDK access while go-livepeer exposes its native discovery response.

## Signer authorization boundary

The signer calls `/v1/compat/go-livepeer/authorize` with a private deployment credential. The base64 workload SDK token given to the gateway contains a workload access credential, which the gateway forwards in the original Authorization header. The policy denies when:

- the global stop is active;
- the token is unknown;
- the workload is expired, ended, or revoked;
- the signer's initial price exceeds the frozen quote;
- the selected orchestrator conflicts with the observed offer; or
- an already-bound workload is presented with another state ID.
- the signed sequence/timestamp cannot be advanced atomically; or
- attributed fee plus pending exposure and the new reservation would exceed an
  optional immutable workload ceiling.

On success, `auth_id` is the workload ID. That deliberate identity makes downstream metering attribution inspectable and compatible with the pinned signer event.

For budgeted workloads, the core conservatively reproduces the pinned
go-livepeer v0.9.2 billing quantity from the job type and signed state timing.
It stores cumulative authorized fee and reconciles pending exposure as metering
arrives. This is authorization policy, not custody or settlement. Unbudgeted
workloads retain the compatibility path.

## Metering boundary

The supervised consumer reads the dedicated signer topic, ignores unrelated event types, strictly decodes `create_signed_ticket`, verifies redundant timestamp fields, and normalizes the quantity. Pixels take precedence for pixel-priced work. Time-priced work uses nanoseconds internally so quote cost is calculated with an exact ceiling.

Events are idempotent by derived usage ID. Known authorization/workload evidence becomes matched usage; unknown evidence becomes unmatched usage visible to administrators. Kafka is transport evidence, not the source of account ownership or quote truth.

## Web architecture

`admin-web` and `user-web` are independent Lit applications. Both use Effect services for typed fallible I/O and Effect Schema at JSON boundaries. The shared `och-app-shell` owns accessible responsive navigation.

Global CSS owns design tokens and the Pymthouse-inspired dark zinc/emerald visual language. Shadow DOM encapsulates components, while inherited custom properties plus explicit `part`/`exportparts` channels let global styles control presentation. Native semantic elements own document meaning and interaction.

## Extension model

Enterprise layers should use these stable seams:

- versioned HTTP resources for users, credentials, offers, workloads, usage, and costs;
- versioned signer-event and core-event schemas;
- typed identity, discovery, policy, event-sink, and storage ports; and
- explicit dependency injection in a deployment-owned composition root.

Candidate external layers include organization tenancy, RBAC, commercial balances, invoicing, OpenMeter export, replicated PostgreSQL, audit retention, and support consoles. Those layers may enrich or consume core records but must not change quote immutability, authorization identity, or usage idempotency.
