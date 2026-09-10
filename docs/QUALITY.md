# Quality policy

Correctness is enforced by executable feedback loops rather than prose alone.

## Required gates

- Formatting and linting for Python, TypeScript, CSS, Markdown, YAML, shell,
  Dockerfiles, OpenAPI, and event schemas.
- Strict static types and architectural dependency tests.
- Unit, property, workflow, database, Kafka, contract, browser, accessibility,
  migration, and Compose smoke tests.
- At least 85% lines, statements, functions, and branches independently for the
  Python backend, admin web, and user web. Aggregating projects cannot hide a
  weak codebase.
- Generated OpenAPI, schemas, migrations, and custom-elements manifests must be
  current and reproducible.
- Dependency review, secret scanning, CodeQL, container scanning, SBOM, and
  release provenance.

## Test principles

- Prefer pure domain tests and deterministic clocks/randomness.
- Test idempotency, conservation, monotonicity, bounds, and round trips as
  properties, especially for ledger and schema code.
- Metering tests cover duplicate Kafka delivery, dropped confirmation followed
  by sequence advancement, a missing final confirmation, fee mismatch, state
  forks, out-of-order sequence, late arrival, and concurrent authorization.
- Use fakes behind ports in unit tests and real dependencies in focused
  integration tests. Do not mock internal implementation details.
- Every production defect gains a regression test.

Beads task `och-u8d.13` owns implementation of the complete gate set.

## Local and required CI entry points

`make test` is the complete local aggregate. The required GitHub check runs the
same bounded Make targets independently so one weak codebase cannot be hidden by
another:

- `quality-repository`, `quality-python`, and `test-backend-unit` cover the
  harness, dependency architecture, format, lint, strict types, and unit tests.
- `test-migrations` proves a fresh PostgreSQL 18 head-to-base-to-head cycle and
  invokes `test-migration-matrix` to exercise populated 0005→0006,
  0006→0007, and 0007→0008 transitions. The matrix also verifies that each
  destructive downgrade refusal leaves the revision and protected rows
  unchanged. `test-backend-live` runs the merged PostgreSQL/Redpanda suite and
  enforces all four backend coverage metrics.
- `quality-frontend`, `test-admin-web`, `test-user-web`, and `test-shared-web`
  enforce frontend architecture and independent coverage thresholds.
- `quality-contracts` checks canonical OpenAPI/AsyncAPI references, exact
  FastAPI method/path parity, schema tests, and reproducible custom-elements
  metadata.

The `Required quality aggregate` job always runs and succeeds only when every
required job succeeded. Third-party Actions are pinned to immutable full commit
SHAs, the workflow has read-only repository permission, and pull-request code is
never run with privileged `pull_request_target` semantics.

## Disposable distribution qualification

`make qualification-harness` builds and fingerprints the six release images,
then starts the keyless services from empty Compose volumes under a unique
project name. It uses an owner-only temporary secret directory and a private,
token-protected fake Resend endpoint; PostgreSQL, Redpanda, and the API remain
unpublished. The target writes bounded sanitized evidence to
`tmp/qualification/harness.json` (or `QUALIFICATION_EVIDENCE`) and always removes
only its labeled containers, networks, and volumes.

This harness proves build, migrations, bootstrap, dependency readiness, both
web applications, edge routing, and fake-mail reachability. It builds and
fingerprints the pinned remote-signer image but explicitly records signer
runtime as unqualified. Funded signer and protocol qualification is a separate
operator-aware exercise owned by the end-to-end qualification milestone.

`make qualification-journey` extends that disposable fixture through real
production routes. It signs in the bootstrap operator and a tenant-linked
credential holder through the protected fake Resend mailbox, creates the
tenant/account/principal/rate/policy/grant/credential/session chain, calls the
go-livepeer compatibility authorization endpoint, and publishes its matching
confirmation through Redpanda. The gate requires one authoritative charge,
the exact ledger balance, duplicate-event and request idempotency, tenant
isolation, kill-switch denial and reopening, and live Playwright assertions in
both production web images. Evidence is bounded and excludes OTPs, cookies,
credentials, signer-session tokens, and webhook secrets.

`make qualification-evidence` is the release-level aggregate. It reruns the
journey and recovery exercises, applies an explicit small reference-fixture
latency/throughput gate, records exact component, image, contract, and schema
identities, exercises clean upgrade/rollback/re-upgrade and populated migration
guards, and creates and restores an encrypted backup in a separate ephemeral
database. It emits sorted JSON plus an allow-listed Markdown summary. The
capacity result is deliberately marked as a reference health gate, not a
production sizing claim; see the [qualification evidence runbook](operations/qualification-evidence.md).
