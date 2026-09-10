# Threat model v1

## Scope and assumptions

This model covers the reference Python API and consumer, PostgreSQL, Redpanda,
unmodified go-livepeer remote signer, admin and user web applications, and the
seven adapter ports. The signer, database, and broker are private services.
TLS termination, host hardening, chain RPC correctness, and the operator's
legal compliance remain deployment responsibilities.

The model uses spoofing, tampering, repudiation, information disclosure,
denial of service, and elevation of privilege as review lenses. Financial
integrity and bounded signer exposure are the highest-impact assets.

## Assets and actors

| Asset | Required property |
| --- | --- |
| Signer key and password | Confidential, non-exportable through the clearinghouse, rotatable |
| Credentials, OTPs, OAuth sessions | Confidential, scoped, expiring, replay-resistant |
| Account balances and open exposure | Correct under concurrency and dependency failure |
| Usage, charges, grants, ledger, audit | Append-only, attributable, idempotent, retained |
| Tenant identity and personal data | Isolated, minimized, erasable where legally permitted |
| Adapter configuration and secrets | Startup-validated, authenticated, never logged |

Actors are credential holders, tenant administrators, clearinghouse operators,
signer instances, adapter operators, and unauthenticated internet clients. A
compromised browser, gateway, adapter, broker client, or network peer is treated
as hostile. PostgreSQL and the configured signer identity are authoritative
only after authenticated connection and boundary decoding.

## Entry points and trust transitions

| Entry point | Trust transition | Required controls |
| --- | --- | --- |
| Browser APIs | Internet to clearinghouse | TLS, origin policy, CSRF defense, secure cookies, rate limits, schema decoding |
| Email OTP request/verify | Internet to identity | Uniform responses, hashed codes, attempt and send limits, short expiry |
| Google/GitHub callback | Provider to identity | State, PKCE, OIDC nonce where applicable, exact redirect and issuer checks |
| Identity invitation | Administrator to verified source identity | Opaque source binding, CSRF, single-use secret hash, scope/role/issuer snapshots, supersession, transactional revalidation |
| Operator bootstrap | Deployment secret to identity | Zero-operator table lock, paired secret configuration, exact idempotent fingerprint, session revocation, fail-closed conflict |
| Signer authorization callback | Private signer to API | Dedicated shared secret or mTLS, timeout, every-call evaluation, stable server-owned `auth_id` |
| Kafka consumer | Broker to metering | Broker authentication, configured producer identity, schema decoding, durable quarantine |
| Admin API | Operator browser to core | Explicit operator role, CSRF defense, reason, confirmation, immutable audit |
| Python adapter entry point | Trusted image to core | Immutable install, manifest/version/capability checks, no environment-driven imports |
| HTTP adapter bridge | Remote service to core | TLS/mTLS or bearer secret, deadlines, typed errors, circuit bounds |

Forwarded headers inside the signer callback body authenticate the user session,
not the signer transport. Kafka envelope `gateway` values are attribution data,
not producer authentication.

## Threats, mitigations, and verification

| Threat | Impact | Mitigation | Verification owner |
| --- | --- | --- | --- |
| Tenant identifier substitution or object enumeration | Cross-tenant disclosure/mutation | Resolve tenant scope from authenticated principal; include tenant predicates in repositories; return indistinguishable not-found results | `och-u8d.6`, `.10`, `.11`, `.13` |
| Credential database theft | Account takeover | Store only memory-hard or keyed hashes; reveal secrets once; prefix lookup plus constant-time verification; rotation and revocation | `och-u8d.6`, `.13` |
| OTP guessing, flooding, or account discovery | Account takeover/abuse | Random six-or-more digit code, keyed hash, short expiry, attempt/send/IP/address limits, uniform accepted response | `och-u8d.5`, `.13` |
| OAuth login CSRF, code interception, or account confusion | Account takeover | State, PKCE, nonce for OIDC, exact issuer/audience/redirect validation, stable provider subject, explicit account-link policy | `och-u8d.5`, `.13` |
| Browser session theft or fixation | Privilege theft | Rotating opaque server session, Secure/HttpOnly/SameSite cookie, renewal after auth, logout revocation, inactivity and absolute expiry | `och-u8d.5`, `.13` |
| CSRF on grants, caps, credentials, or kill switch | Financial/admin mutation | SameSite cookie plus origin-bound CSRF token; no state-changing GET; explicit action reason | `och-u8d.5`, `.6`, `.10`, `.13` |
| Signer webhook spoofing | Unauthorized reservation or denial | Private network and authenticated callback; signer ID comes from transport configuration; reject unknown identities | `och-u8d.7`, `.12`, `.16` |
| Forwarded `Signer-Auth-Id` spoofing | Misattributed spend | Always return clearinghouse-owned stable `auth_id`; never trust the forwarded value as identity | `och-u8d.7`, `.13` |
| Cached signer allow bypasses suspension/kill switch | Unbounded interval of spend | Return `expiry: 0` on every successful callback and test repeated calls | `och-u8d.7`, `.19` |
| State replay, fork, or concurrent sequence | Duplicate/overspend | Serialize `(signer_id, StateID)`; unique sequence receipt; hash state; reject non-identical reuse and out-of-order state | `och-u8d.7`, `.8`, `.13` |
| Kafka duplication, reordering, poison input, or drop | Double charge or missing charge | Transport dedupe, semantic sequence checks, transactional settlement, durable quarantine, pending holds, reconciliation | `och-u8d.8`, `.18`, `.19` |
| Fee/quantity overflow or float drift | Financial corruption | Decimal-string wire values, unbounded integers/exact rationals, bounded input sizes, conservative reservation, signer fee preservation | `och-u8d.3`, `.7`, `.8`, `.13` |
| Ledger or idempotency race | Overspend/double posting | Database serialization and unique constraints; balanced append-only transaction; conflicting key reuse rejected | `och-u8d.6`, `.7`, `.8`, `.13` |
| Signer key copied into API/image/log | Wallet compromise | Clearinghouse never accepts decrypted key material; encrypted read-only signer mount or secret injection; redaction tests | `och-u8d.12`, `.16`, `.13` |
| Malicious or incompatible adapter | Data theft/corruption | Trusted immutable installs only; explicit wiring; version/capability negotiation; least-privilege credentials; conformance tests | `och-u8d.3`, `.13` |
| Resource exhaustion | Availability loss | Request/body/list bounds, per-identity/IP rate limits, DB pool limits, bounded queue/concurrency, deadlines, load tests | `och-u8d.4`, `.5`, `.8`, `.13`, `.19` |
| Audit deletion or forged repudiation | Unprovable operator action | Append-only audit rows with actor, target, reason, request ID and time; restricted DB role; backup verification | `och-u8d.6`, `.18`, `.19` |
| Supply-chain replacement | Code/key compromise | Lockfiles, pinned images/actions, review, CodeQL, dependency audit, image scan, SBOM, provenance and checksums | `och-u8d.13` |

## Authorization defaults

The system denies when identity, policy, store, ledger, signer binding, or
reservation calculation is unavailable or ambiguous. Kill-switch evaluation
and account/session/lease status occur inside the same serialized decision as
the reservation. Error bodies use stable codes and contain no account-existence
or secret detail.

Operator and tenant-administrator powers are distinct. Only operators can
change the global kill switch, platform adapter configuration, or cross-tenant
grants. Tenant administrators cannot grant themselves operator access or move
value between tenants.

## Accepted residual risks

- Unmodified go-livepeer can drop its asynchronous Kafka observation. Pending
  reservations and sequence reconciliation expose this; the final event of a
  session can require operator resolution.
- A compromised signer can sign outside the clearinghouse entirely. Network,
  key custody, wallet monitoring, and funding limits constrain that operator
  risk; this service cannot cryptographically prevent it.
- Email account or OAuth-provider compromise defeats that authentication
  factor. Operators may place stronger external identity adapters in front of
  high-value deployments.
- External chain RPC and orchestrator pricing can be incorrect or unavailable.
  Price ceilings, pinned network configuration, health checks, and limited
  signer funding bound but do not eliminate the risk.

These risks must appear in deployment documentation and operator-facing health;
they are not represented as guarantees in product copy.

## Review triggers

Re-review this model when a trust boundary, authentication method, payment
shape, signer version, adapter source, public endpoint, custody mechanism,
financial unit, or retention category changes, and after any relevant incident.
