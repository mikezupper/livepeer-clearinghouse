# Observability, service levels, and alerts

## Scope and safety

Application logs, metrics, and traces use OpenTelemetry and bounded attributes.
Allowed dimensions are route templates, HTTP method, status family, controlled
decision/reason values, component, adapter, auth mode, and job type. Tenant,
account, principal, session, signer state, topic, partition, email, IP address,
tokens, secrets, raw payloads, and arbitrary exception text are forbidden as
metric labels.

The optional local stack is ephemeral and private. Start it with
`make observability-up`, run `make ops-status`, and stop it with
`make observability-down`. It is for developer and incident exercises, not a
production monitoring system. `observability-down` preserves its named volume;
use the repository's guarded `make destroy DESTROY=1` only when that data may be
deleted with the rest of the Compose project.

## Initial objectives

| Signal | Objective | Page when |
| --- | --- | --- |
| Signer authorization decisions | 99.95% availability; p95 below 150 ms; p99.9 below 500 ms | Five-minute availability burn or latency breach |
| Authenticated API | 99.9% availability; p95 below 500 ms | Multi-window error-budget burn |
| Pending settlement age | No pending receipt older than 60 s; none unresolved | Warn at 60 s; page at 5 min or immediately on unresolved state |
| Integrity | No unexplained gap, fork, balance drift, or unauthorized repair | Any occurrence; zero error budget |

Also alert on stale consumer heartbeat, rising broker lag, producer drop/error
signals, poison observations, unresolved exposure amount/age, settlement
failure, authentication abuse, database pool exhaustion/readiness, adapter
failure, signer funding/withdrawal state, backup verification age, retention
job failure, and kill-switch changes.

## Signal and query catalog

The collector converts OpenTelemetry dots to Prometheus underscores and adds
`_total` to counters. These are the initial reference alerts; replace warning
capacity thresholds only after recording a deployment load baseline. A zero
error budget means any occurrence pages.

| Condition | PromQL | Initial action threshold |
| --- | --- | --- |
| Signer availability | `sum(increase(clearinghouse_signer_authorization_decisions_total{outcome="unavailable"}[5m]))` | `> 0`; follow signer and database runbooks |
| Signer denial mix | `sum by (reason) (increase(clearinghouse_signer_authorization_decisions_total{outcome="denied"}[5m]))` | Investigate a new reason or twice the recorded baseline |
| Signer callback latency | `histogram_quantile(0.95,sum by (le) (rate(clearinghouse_http_request_duration_seconds_bucket{route="/v1/compat/go-livepeer/authorize"}[5m])))` | `> 0.15` seconds; p99.9 uses `0.999` and pages above `0.5` |
| Exposure utilization | `max(clearinghouse_exposure_open_utilization)` | Warn `> 9000` basis points; stop admission at the configured hard limit |
| Pending/unresolved exposure | `max(clearinghouse_exposure_pending_utilization)` and `max(clearinghouse_exposure_unresolved_utilization)` | Warn pending `> 8000`; page on unresolved `> 0` |
| Consumer lag | `max(clearinghouse_metering_consumer_lag)` | Page if `> 0` for five minutes or growing across three exports |
| Consumer heartbeat | `max(clearinghouse_metering_consumer_heartbeat_age_seconds)` | Page above `CLEARINGHOUSE_METERING_HEARTBEAT_STALE_SECONDS` (60 seconds by default) |
| Dropped/poison evidence | `sum(increase(clearinghouse_metering_consumer_dropped_total[5m]))` | `> 0`; inspect quarantine and reconcile |
| Retention gap | `sum(increase(clearinghouse_metering_consumer_gaps_total[5m]))` | `> 0`; stop admission and follow metering integrity recovery |
| Settlement failure | `sum(increase(clearinghouse_metering_settlement_failures_total[5m]))` | `> 0`; keep reservations pending and reconcile |
| Oldest pending settlement | `max(clearinghouse_metering_settlement_pending_oldest_age_seconds)` | Warn above 60 seconds; page above five minutes |
| Oldest unresolved settlement | `max(clearinghouse_metering_settlement_unresolved_oldest_age_seconds)` | `> 0`; page and reconcile immediately |
| Authentication abuse | `sum by (adapter) (increase(clearinghouse_auth_abuse_total[5m]))` | Investigate above the deployment baseline; reference warning is `> 10` |
| Database errors | `sum(increase(clearinghouse_database_operations_total{outcome="unavailable"}[5m]))` | `> 0`; check readiness and PostgreSQL |
| Adapter state | `min by (component,adapter) (clearinghouse_adapter_health_ratio)` | `< 1` for two export intervals |

Run `make ops-status` alongside every telemetry query; it is the authoritative
current snapshot for migration head, exact consumer binding/heartbeat,
reconciliation, backup age, retention jobs, and the kill switch. Counter queries
must use `increase` or `rate`; never alert on a cumulative counter's raw value.

The local stack has no host telemetry port. Query it from its private network,
replacing `.env` with the selected safe environment file:

```sh
docker compose --env-file .env --profile observability exec -T observability \
  curl --fail --silent --get \
  --data-urlencode 'query=max(clearinghouse_metering_consumer_heartbeat_age_seconds)' \
  http://127.0.0.1:9090/api/v1/query

docker compose --env-file .env --profile observability exec -T observability \
  curl --fail --silent --get \
  --data-urlencode 'query={service_name="clearinghouse-api"} |= "error"' \
  --data-urlencode 'limit=50' http://127.0.0.1:3100/loki/api/v1/query_range

docker compose --env-file .env --profile observability exec -T observability \
  curl --fail --silent --get \
  --data-urlencode 'q={ resource.service.name = "clearinghouse-api" && status = error }' \
  --data-urlencode 'limit=20' http://127.0.0.1:3200/api/search
```

Queries use only bounded service, component, adapter, route, outcome, and reason
dimensions. Correlate a selected trace with sanitized `trace_id`/`request_id`
logs; do not add tenant, principal, session, topic, partition, or raw error text
as labels. `make observability-check` proves that an application metric, log,
and trace are each queryable before relying on this catalog.

## Triage

1. Run `make ops-status` and record the release/schema revision and bounded
   component codes. Query the affected time window by trace or request ID.
2. Confirm whether admission is already stopped. Activate the kill switch when
   integrity is uncertain; do not release pending value.
3. Correlate authorization latency/denials, database health, consumer
   heartbeat, broker lag, open reconciliation cases, and adapter state.
4. Follow the component runbook. Escalate immediately for unexplained
   financial drift, secret exposure, lost authoritative data, or signer-key
   uncertainty.

Expected result: every alert maps to a bounded signal and a named action.
Abort: if a query requires unbounded identifiers or raw payloads, stop and use
the operator API or PostgreSQL evidence procedure instead. Validate recovery by
returning public readiness to `ok`, clearing the triggering SLI, and attaching a
sanitized exercise record to the incident Bead.
