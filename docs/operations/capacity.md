# Capacity qualification

Capacity results are topology-specific. The single-node reference Compose stack
is a reproducible baseline, not a production sizing recommendation.

The shipped limits are safety bounds, not measured throughput claims:

| Boundary | Reference value |
| --- | --- |
| Redpanda | one node, one partition, one vCPU shard, 768 MiB process memory |
| Broker retention | at least seven days; delete-only topic policy |
| API/worker PostgreSQL pool | 10 connections per process by default, configurable 1–100 |
| Metering payload | 1 MiB default, hard maximum 8 MiB |
| Reconciliation/retention batch | 100/200 defaults, hard maximum 1,000 |
| Authorization SLI target | p95 below 150 ms and p99.9 below 500 ms |

Run `make capacity` against a running stack to capture only current PostgreSQL
size/connection inventory and broker topic policy. It does not generate load or
certify an SLO. Run fault targets only in an `och-ops-test-*` disposable Compose
project with synthetic identities and no funded signer.

Before production, use a deployment-owned load harness and record CPU/memory,
PostgreSQL pool and storage, broker retention/storage, payload distribution,
concurrency, duration, release/schema revision, and host characteristics. The
repository intentionally does not ship a generic harness whose results could be
mistaken for sizing evidence.

That external qualification should exercise at least twice the declared
expected peak authorization rate and
metering rate. Measure p50/p95/p99/p99.9 authorization latency, denial mix,
database pool wait, transaction/lock time, consumer throughput/lag, heartbeat
age, settlement age, open/pending/unresolved exposure, errors, drops, and
shutdown drain time. Include a steady-state phase, burst, consumer restart, and
graceful SIGTERM.

Pass that deployment qualification only when authorization p95 is below 150 ms
and p99.9 below 500 ms, no
integrity error/drop occurs, lag and settlement age return within objective,
and restart/replay is idempotent. Abort immediately on unbounded resource
growth, projection drift, dropped evidence, failed checks, or contact with a
non-disposable environment. Preserve only aggregate sanitized results and
create Beads for regressions; never commit raw payloads or secrets.
