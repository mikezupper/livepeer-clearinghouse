# Security model

The versioned [threat model](security/threat-model.md) and
[data lifecycle policy](security/data-lifecycle.md) are normative for the
reference distribution. Deployment operators remain responsible for adapting
retention and incident contacts to their jurisdiction.

## Trust boundaries

- Browser traffic is untrusted and terminates at the clearinghouse API.
- The signer and Kafka broker live on private networks. The signer admin port is
  never published and signing access is protected by the clearinghouse webhook.
- The webhook always returns zero authorization expiry so go-livepeer cannot
  cache an allow decision across payment calls. Callback credentials identify
  the signer; forwarded user headers are never trusted as signer identity.
- External adapters are untrusted until authenticated, version checked, and
  decoded at the port boundary.
- PostgreSQL is authoritative for identity, exposure, idempotency, and ledger
  state.

## Authentication

Email sign-in uses a short-lived, attempt-limited, single-use OTP. Only the OTP
hash is stored; responses do not reveal whether an account exists. Resend sends
mail through a configurable API URL but does not own authentication state.

Google and GitHub OAuth are disabled unless explicitly enabled with complete
configuration. Flows use state and PKCE; OIDC also uses nonce and issuer/audience
validation. Stable provider subject IDs anchor identities. All methods converge
on rotating, database-backed HttpOnly secure sessions.

Implementation and deployment details are documented in the
[authentication design](design-docs/authentication.md).

## Secrets and custody

Runtime secrets come from environment configuration or a deployment secret
store. `.env` is local-only and ignored; `.env.example` contains no usable
secret. Credentials and signer keys are never committed, returned twice, or
logged. Production signer keystores are encrypted and mounted read-only where
go-livepeer supports it.

The pinned signer logs its RPC URL without redaction. Custom provider paths and
query credentials are accepted at the project owner's direction, so container
logs must be treated as potentially credential-bearing and tightly restricted.
A credential-free private relay remains the safer production topology. See
[signer operations](operations/signer.md) for the accepted-risk boundary,
secret mounts, private admin access, and funding-readiness checks.

## Authorization

Operator, tenant administrator, and credential-holder capabilities are explicit
and tenant scoped. Every privileged mutation records actor, reason, request ID,
and immutable audit data. Admission, ledger, or identity uncertainty fails
closed.

Signer authorization is serialized by signer state and sequence. Repeated,
forked, out-of-order, untyped, or fee-inconsistent requests fail closed or enter
quarantine; they never consume a second charge silently.

The public vulnerability reporting process belongs in root `SECURITY.md`,
created by Beads task `och-u8d.2`.
