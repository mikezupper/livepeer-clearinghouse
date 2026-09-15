# Threat model v1

## Scope and assumptions

This model covers the four-service reference distribution: the edge, one
Python core process containing the API and Kafka consumer, Redpanda, the
unmodified pinned go-livepeer remote signer, durable SQLite and signer volumes,
and the separate admin and user web applications served by the edge. The core
is a single-node access and metering system, not a balance, billing, settlement,
or high-availability system.

Only the edge is published to the host. Core, SQLite, Redpanda, and signer
administration remain private. TLS termination, host and container hardening,
chain RPC correctness, custody funding limits, backups, and legal compliance
remain deployment responsibilities. External storage and enterprise services
are outside this reference trust boundary.

The model uses spoofing, tampering, repudiation, information disclosure, denial
of service, and elevation of privilege as review lenses. Signer custody,
account isolation, authorization integrity, and honest attribution of received
usage evidence are the highest-impact assets.

## Assets and actors

| Asset | Required property |
| --- | --- |
| Signer key and password | Confidential, mounted read-only, absent from the core and web images |
| OTPs, sessions, OAuth state, account API credentials, and workload access credentials | Keyed-digest storage, bounded lifetime or revocation, no logging |
| Personal account and workload data | Scoped to the authenticated account |
| Price observations and workload quotes | Exact, attributable, immutable after workload creation |
| Authorization records | Fail-closed, state-bound, idempotent |
| Signer usage evidence | Strictly decoded, deduplicated, honestly marked matched or unmatched |
| Global stop | Administrator-only and evaluated for every new authorization |
| Configuration and volumes | Startup-validated, least privilege, backed up by the operator |

Actors are authenticated users, the configured administrator, account API credential
holders, the remote signer, broker clients, discovery endpoints, identity and
email providers, and unauthenticated internet clients. A compromised browser,
SDK client, discovery endpoint, broker producer, signer, or network peer is
treated as hostile. SQLite is authoritative only after schema validation and
transactional reads; Kafka payload metadata never authenticates a producer.

## Entry points and trust transitions

| Entry point | Trust transition | Implemented controls |
| --- | --- | --- |
| Browser APIs | Internet through edge to core | Exact Origin allowlist, same-site HTTP-only session cookie, matching CSRF cookie/header for mutations, schema decoding, private response caching disabled |
| Email OTP request and verify | Internet to Resend-compatible delivery and core | Normalized email, six-digit code, keyed digest, ten-minute default expiry, five-attempt default, generic invalid-code response |
| Google or GitHub callback | Provider to core | Disabled unless fully configured, authorization code with PKCE, one-shot expiring state, exact state cookie, Google nonce validation, verified provider email |
| Account API credential | Client to core control plane | Opaque returned-once `och_live_…` value, keyed digest, account ownership, revocation |
| Workload SDK token | Gateway client to one quoted workload | Returned-once encoded configuration, workload-specific credential, workload expiry and revocation |
| Discovery endpoints | Network to core | HTTP boundary normalization, bounded observation lifetime, exact rational price, immutable workload snapshot |
| Signer authorization callback | Private signer through edge to core | Independent bearer secret, constant-time comparison, forwarded user token treated separately, every-call global-stop and workload evaluation, `expiry: 0` |
| Kafka consumer | Broker to core | Dedicated configured topic/group, strict pinned event decoder, transport identity deduplication, commit after ingestion |
| Admin API | Configured administrator browser to core | Session identity derived from configured normalized email, CSRF protection on mutation, no client-selected role |
| SQLite volume | Core process to durable state | Private mount, foreign keys, WAL, busy timeout, transactional initialization, serialized writer boundary |

Forwarded headers inside the signer callback body carry the workload credential;
they do not authenticate the signer transport. Kafka `gateway` fields are
attribution data, not producer authentication. Broker network policy and topic
ACLs are deployment controls when the private reference broker is replaced.

## Threats, mitigations, and verification

| Threat | Impact | Current mitigation and verification boundary |
| --- | --- | --- |
| Account identifier substitution or object enumeration | Cross-account disclosure or mutation | Account scope comes from the authenticated browser session or account API credential; workload and credential mutations verify ownership; boundary tests exercise cross-account denial |
| Credential database theft | Account or workload takeover | Only keyed digests are stored for OTP, session, CSRF, account API, OAuth-state, nonce, and workload access secrets; plaintext account API credentials and encoded workload SDK tokens are returned once |
| OTP guessing or replay | Account takeover | Six random digits, keyed digest, bounded expiry and attempts, challenge consumption on success; deployment edge rate limiting remains required for Internet exposure |
| Email enumeration or OTP flooding | Privacy loss or delivery abuse | Request returns an empty accepted response; the core does not yet provide IP/address send throttling, so production edge/provider controls are required |
| OAuth login CSRF, interception, or account confusion | Account takeover | PKCE, random one-shot state bound to an HTTP-only cookie, ten-minute flow expiry, Google nonce check, stable provider flow and verified email requirement |
| Browser session theft or CSRF | Account/admin mutation | Opaque expiring session, Secure cookie required in production, SameSite=Lax, exact allowed Origin and double-submit CSRF value checked against the stored digest |
| Account API credential theft | Account-scoped control-plane API use | Opaque scoped credential, keyed digest and revocation; clients must protect the returned-once value and use TLS |
| Workload SDK token theft | Signer access for one quoted workload | The embedded credential is scoped to one immutable workload, expires with it, and is denied after workload revocation; clients must use TLS and avoid sharing the token |
| Signer webhook spoofing | Unauthorized signer decision | Independent webhook bearer secret, constant-time comparison, private topology, and no reliance on forwarded identity headers |
| Forwarded workload-token spoofing | Unauthorized spend | Token must resolve to an active, unexpired workload; failed lookup denies without revealing account data |
| Cached allow bypasses revocation or stop | Continued authorization | Every success returns `expiry: 0`; the global stop and workload state are evaluated on every callback |
| State rebinding, replay, or concurrent fork | Misattribution or duplicate spend exposure | First success binds one workload to one unique signer state; exact latest-sequence retries are idempotent; stale, skipped, or conflicting sequences fail closed in one writer transaction |
| Metering delay permits ceiling overspend | Spend exceeds user policy before Kafka reconciliation | A budgeted callback conservatively reserves its pinned job-type fee before tickets are returned; authorization compares the greater of attributed and cumulatively authorized fee plus the new reservation with the immutable ceiling |
| Discovery tampering or stale price | Incorrect authorization ceiling | Observations expire; workload creation copies exact runner, capability and price terms; authorization denies price increases and selected-orchestrator mismatch. TLS and discovery-source trust remain operator responsibilities |
| Kafka duplicate or unrelated input | Duplicate or corrupt usage | Strict pinned event decoding, unrelated-type filtering, deterministic usage identity and unique transport event ID |
| Kafka loss, spoofing, or poison input | Missing, false, or delayed usage evidence | Private dedicated broker by default; external deployments must authenticate producers. The core reports only received evidence and never claims billing completeness; rejected poison input must be diagnosed from core readiness/logging |
| Unknown `auth_id` | Cross-account attribution | Evidence is retained as `unmatched` without account, user, workload, or authorization ownership |
| Fee, quantity, or price rounding drift | Incorrect cost display | Non-negative integers and exact rational prices; quote-derived cost uses integer ceiling; signer-reported fee remains a separately labelled value |
| SQLite corruption, theft, or concurrent writers | Availability loss or personal-data disclosure | Private persistent volume, one supervised writer process, transactional initialization, integrity-checked restore and operator-controlled filesystem permissions/encryption |
| Signer key copied into core, image, or logs | Wallet compromise | Core never accepts custody material; encrypted V3 key and password are external read-only mounts. The pinned signer may log its RPC URL, so credential-free URLs and protected logs are required |
| Resource exhaustion | Availability loss | Schema bounds, workload TTL bounds and the single-writer architecture limit individual operations; production edge request/body/concurrency limits and capacity monitoring remain operator controls |
| Supply-chain replacement | Code or key compromise | Lockfiles, pinned actions/images, required review and quality checks, dependency/CodeQL scans, image scans, SBOMs, signatures, attestations, and release checksums |

## Authorization defaults

The core denies signer authorization when the webhook credential, workload
credential, global stop, workload state or expiry, quote ceiling, selected
orchestrator, existing state binding, sequence/timestamp, or optional remaining
spend is invalid or ambiguous. It returns no cache interval. Pending exposure
is an authorization reservation only: the core does not move funds, maintain a
custody balance, or claim that a successful decision guarantees later Kafka
evidence.

Admin access is computed from `CLEARINGHOUSE_ADMIN_EMAIL` when the identity is
resolved. The core has no tenant administrator, invitation, role hierarchy,
bootstrap operator, grants, or runtime adapter catalog. Extension ports are
selected explicitly by a deployment composition root; external layers do not
receive direct access to core tables.

## Accepted residual risks

- Unmodified go-livepeer produces Kafka observations asynchronously. Broker
  failure, process loss, or queue exhaustion can lose the final event. The core
  exposes received, unmatched, and divergent evidence but cannot reconstruct an
  event it never received.
- A compromised signer can sign outside the Clearinghouse entirely. Network
  isolation, encrypted custody, wallet monitoring, and funding limits constrain
  this operator risk; the core cannot cryptographically prevent it.
- A compromised email account or OAuth provider defeats that authentication
  factor. High-value deployments may add stronger identity controls outside the
  core's versioned boundary.
- The reference broker and SQLite profile are single-node. A process, disk, or
  host failure can interrupt authorization and metering until recovery.
- External chain RPC and discovery data can be incorrect or unavailable.
  Frozen price ceilings, selected-orchestrator checks, short observation expiry,
  health checks, and limited signer funding bound but do not eliminate the risk.
- The core has no built-in Internet-facing rate limiter, automated backup
  service, long-term audit system, or billing reconciliation. Operators must
  supply these controls where their risk model requires them.

These residual risks must appear in deployment decisions and operator-facing
monitoring. Product copy must not present received usage evidence as complete
settlement or the single-node profile as highly available.

## Review triggers

Re-review this model when a trust boundary, authentication method, public
contract, storage implementation, signer version, Kafka schema, custody
mechanism, price unit, or exposed endpoint changes, and after any relevant
incident.
