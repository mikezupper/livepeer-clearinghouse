# Production-ready walking slice

## Motivation

Builders need to spend on the Livepeer network without each application
operating a funded wallet, billing system, signer, and identity bridge. Open
Clearinghouse extracts the small authorization-and-settlement core from
Pymthouse and makes commercial integrations optional.

## Users

- Operators configure exposure, pricing, grants, adapters, and emergency stops.
- Tenant administrators manage accounts, principals, credentials, and caps.
- Credential holders authenticate, create signer sessions, inspect usage, and
  understand charges and balances.
- Signers request authorization and publish successful usage.

## Required journeys

- Sign in using a Resend-delivered email OTP. Google and GitHub OAuth appear
  only when fully configured.
- Create and suspend tenants, accounts, and principals.
- Issue, rotate, and revoke opaque credentials whose secret is shown once.
- Post audited, idempotent credit/debit/adjustment grants.
- Publish static rate cards and a policy-shaped capability catalog.
- Open a capped, expiring lease and mint a short-lived signer session.
- Authorize go-livepeer payment calls and fail closed on unavailable policy or
  ledger state.
- Reserve supported typed signer payment calls synchronously, ingest signer
  Kafka confirmations, reconcile sequence gaps, settle leases, and expose
  reproducible charges and double-entry balances.
- Activate a global kill switch with actor and reason.

## Reference deployment

The default Docker Compose stack includes PostgreSQL, Redpanda, the Python API
and consumer, go-livepeer remote-signer, admin web, and user web. Make targets
are the supported interface for building, testing, and operating it locally.

## Non-goals

- OpenMeter, Stripe, Auth0, Turnkey, marketplace, merchant resale, and support
  console features.
- The optional `/v1/jobs` gateway in the first walking slice.
- Untyped go-livepeer `inPixels` payment requests, because the unmodified auth
  callback does not expose enough data to reconstruct their reservation safely.
- Runtime installation of untrusted adapter code.
- Replacing PostgreSQL with SQLite in production.
