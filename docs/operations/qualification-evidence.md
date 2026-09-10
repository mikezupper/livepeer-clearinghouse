# Release qualification evidence

`make qualification-evidence` executes the account-to-charge journey, restart
and metering recovery exercise, a clean schema upgrade/rollback/upgrade cycle,
the populated migration guard matrix, and an encrypted backup restored into a
new ephemeral PostgreSQL volume. Every Compose project has a generated,
validated name and cleanup is restricted to that project's labeled resources.

The target writes sorted machine-readable JSON to
`tmp/qualification/release.json` and an allow-listed operator summary to
`tmp/qualification/release.md`. Override those ignored paths with
`QUALIFICATION_EVIDENCE` and `QUALIFICATION_REPORT`. Component manifests for
the journey and recovery exercise are retained under
`tmp/qualification/components/` and cited by SHA-256.

## Capacity boundary

The automated capacity gate sends 32 sequential readiness requests through the
actual edge image with concurrency one. It requires zero errors, p95 no greater
than 1,000 milliseconds, and at least two requests per second. These generous
thresholds detect a broken or pathologically slow reference fixture; they are
not a production sizing claim and do not replace deployment-specific load
testing. The manifest sets `production_sizing_supported` to `false` and records
the workload, sample size, concurrency, thresholds, and p50/p95/p99 and
throughput results.

Production qualification must still apply the workload and SLO method in
[capacity qualification](capacity.md) on the intended topology. In particular,
readiness latency is not signer authorization latency and a single-node
Compose broker or database says nothing about high availability.

## Compatibility and identity

The JSON records the exact configured image reference, local content-addressed
image ID, and available repository digests for PostgreSQL, Redpanda, the Python
services, pinned unmodified go-livepeer, both web applications, Caddy edge, and
operations image. It also records runtime Python, PostgreSQL, Redpanda, and
Caddy versions; pinned Vite, Vitest, and TypeScript versions; OpenAPI and
AsyncAPI versions and SHA-256 hashes; and the current Alembic revision.
go-livepeer's exact binary identity comes from the recovery signer's bounded
`-version` invocation.

Local build tags do not have a registry digest, so their immutable Docker image
ID is authoritative for this exercise. Release CI separately publishes and
signs registry digests, SBOMs, and provenance.

## Interpreting a pass

A pass applies only to the exact manifest and the disposable reference
topology. It proves the encrypted backup's checksums, schema revision, table
counts, ledger balance invariants, and exposure projections survived an
isolated restore. It also proves clean schema rollback and re-upgrade plus
atomic refusal of unsafe populated downgrades. It does not qualify point-in-time
recovery, production RPO/RTO, multi-node failover, or funded signing. Funded
go-livepeer signing remains the operator-only gate documented in
[signer operations](signer.md).
