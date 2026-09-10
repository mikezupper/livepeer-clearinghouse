# Authentication design

Interactive authentication defaults to a Resend-delivered six-digit email
code. Google OIDC and GitHub OAuth are optional and appear in provider
discovery only when explicitly enabled with complete configuration. Every
method resolves a stable provider subject and converges on the same
PostgreSQL-backed browser-session model.

## Email code flow

The API canonicalizes an email address, applies independent keyed-hash rate
limits for the address and direct client IP, and generates a cryptographically
random six-digit code. It sends the code through the official Resend Python SDK
using `CLEARINGHOUSE_AUTH_RESEND_API_URL` and only then replaces the prior
usable challenge. A mail outage therefore consumes abuse budget but does not
invalidate a previously delivered code.

Only keyed SHA-256 hashes of the canonical email, code, and client IP are
stored. A challenge expires within 15 minutes, has a fixed attempt ceiling,
and is consumed under a row lock exactly once. Request responses are identical
for new and existing identities. Raw codes and API keys are never logged.

The Resend SDK exposes its API URL and asynchronous HTTP transport as
process-wide configuration. The composition root creates one sender, sets the
custom URL once, and uses the SDK's `Emails.send_async` operation. Production
requires an HTTPS URL, non-placeholder sender, API key, and strong independent
hash pepper.

## OAuth and OIDC

Both optional providers use OAuth authorization-code flow with PKCE S256 and a
random, single-use state. The initiating browser also receives that state in a
short-lived `Secure`, `HttpOnly`, `SameSite=Lax` flow cookie. A callback must
match both the database hash and browser cookie, preventing login CSRF and
session swapping. Start and callback endpoints are IP rate limited and bound
query values before provider traffic occurs.

Google additionally uses an OIDC nonce. Its ID token signature, RS256
algorithm, issuer, audience, expiry, issued-at time, subject, and nonce are
validated against Google's JWK set with JoseRFC. GitHub identity uses the
stable numeric user ID and accepts only a verified primary email. OAuth
identities are never linked merely because email strings match.

An OAuth transaction is consumed before exchanging the authorization code.
This intentional one-shot rule makes ambiguous provider failures fail closed;
the browser flow cookie is cleared on success, validation failure, rate limit,
or provider outage, and the user starts a fresh authorization attempt.

## Principals and onboarding

A first successful provider identity creates an active but unscoped principal
with no roles. It cannot access tenant, account, or operator capabilities.
An operator or authorized tenant administrator creates a one-time invitation
that binds the verified unscoped source principal's opaque ID to an existing
scoped target principal. The source principal must redeem it in its own
CSRF-protected browser session. The system never matches identities by email
across providers. Issuance snapshots the target scope and roles and the
issuer's authority; redemption revalidates all of them under locks. Issuing a
new invitation supersedes every outstanding invitation for that source.

Successful redemption moves exactly one provider identity, revokes every
source session, suspends the now-empty source principal, and records immutable
link and audit facts without returning the internal identity ID. Database
triggers keep that source as an unscoped, roleless tombstone and prevent the
linked identity from drifting. The user must sign in again to obtain the
target principal's permissions.

For a fresh deployment, setting both
`CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL` and
`CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET` enables a one-shot startup
bootstrap. It is legal only while zero operators exist. The transaction either
creates the configured email identity or promotes its active, unscoped,
single-identity principal, revokes pre-bootstrap sessions, and records a
singleton configuration fingerprint. An exact retry is idempotent; changed
configuration or inconsistent/nonempty operator state fails startup closed.
Remove both settings after successful bootstrap. Neither value nor its stable
fingerprint is logged.

## Browser sessions and CSRF

The browser receives an opaque `och_session` cookie with `Secure`, `HttpOnly`,
`SameSite=Lax`, and a bounded maximum age. PostgreSQL stores only its keyed
hash, the principal reference, hashed IP/user-agent security metadata,
inactivity time, fixed absolute expiry, and revocation time. A readable
`Secure`, `SameSite=Strict` `och_csrf` cookie contains a separate random token;
mutations require the same value in `X-CSRF-Token` plus an exact configured
`Origin`.

Ordinary protected requests atomically validate principal status, revocation,
idle timeout, and absolute timeout while touching the session row. They do not
rotate on every request because parallel browser requests would invalidate one
another. `POST /v1/auth/session/refresh` is the CSRF-protected controlled
renewal point: one database
transaction locks and revokes the old session and inserts fresh session and
CSRF hashes without extending the original absolute lifetime. Logout revokes
server state and expires both cookies.

Use `AuthDependencies.current_principal` for reads and the single
`AuthDependencies.csrf_protected` actor dependency for mutations. A route must
not resolve actor and CSRF as two independent dependencies. Authentication
failures become 401 and CSRF failures become 403 rather than escaping as server
errors.

## Cross-origin policy

`CLEARINGHOUSE_AUTH_ALLOWED_ORIGINS` is a comma-separated exact origin list.
Origins cannot contain credentials, paths, queries, or fragments and must use
HTTPS in production. CORS allows credentials only for those origins, enumerates
methods and request headers, and never uses a wildcard. The success redirect
and every enabled provider callback are also HTTPS-only in production.

See the [threat model](../security/threat-model.md),
[data lifecycle](../security/data-lifecycle.md), and
[OpenAPI contract](../../contracts/openapi.yaml) for normative controls and
wire behavior.
