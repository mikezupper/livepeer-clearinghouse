#!/bin/sh

set -eu

project=${MIGRATION_MATRIX_PROJECT:-livepeer-clearinghouse-migration-matrix}
env_file=${MIGRATION_MATRIX_ENV_FILE:-.env.example}

compose() {
  docker compose -p "$project" --env-file "$env_file" "$@"
}

cleanup() {
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}

psql() {
  compose exec -T postgres sh -ceu \
    'psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    postgres
}

alembic() {
  compose run --rm migrate alembic -c backend/alembic.ini "$@"
}

assert_scalar() {
  expected=$1
  query=$2
  actual=$(printf '%s\n' "$query" | psql)
  if [ "$actual" != "$expected" ]; then
    printf 'migration matrix assertion failed\nexpected: %s\nactual:   %s\n' \
      "$expected" "$actual" >&2
    exit 1
  fi
}

assert_downgrade_refused() {
  target=$1
  expected_error=$2
  fingerprint_query=$3
  before=$(printf '%s\n' "$fingerprint_query" | psql)
  if output=$(alembic downgrade "$target" 2>&1); then
    printf 'destructive downgrade to %s unexpectedly succeeded\n' "$target" >&2
    exit 1
  fi
  case $output in
    *"$expected_error"*) ;;
    *)
      printf 'downgrade to %s failed without the expected refusal\n%s\n' \
        "$target" "$output" >&2
      exit 1
      ;;
  esac
  after=$(printf '%s\n' "$fingerprint_query" | psql)
  if [ "$after" != "$before" ]; then
    printf 'downgrade to %s changed protected revision or rows\nbefore: %s\nafter:  %s\n' \
      "$target" "$before" "$after" >&2
    exit 1
  fi
}

trap cleanup EXIT INT TERM
compose up -d --wait postgres
compose run --rm --build migrate alembic -c backend/alembic.ini upgrade 20260909_0005

psql <<'SQL'
BEGIN;
INSERT INTO tenants(id, display_name)
VALUES ('tenant_migration_fixture', 'Migration fixture tenant');
INSERT INTO accounts(id, tenant_id, display_name, unit, exposure_cap)
VALUES ('account_migration_fixture', 'tenant_migration_fixture',
        'Migration fixture account', 'wei', 1000);
INSERT INTO principals(id, tenant_id, account_id, display_name)
VALUES ('principal_migration_fixture', 'tenant_migration_fixture',
        'account_migration_fixture', 'Migration fixture principal');
INSERT INTO credentials(id, tenant_id, account_id, principal_id, prefix,
                        secret_hash, label)
VALUES ('credential_migration_fixture', 'tenant_migration_fixture',
        'account_migration_fixture', 'principal_migration_fixture',
        'och_migration_fixture', decode(repeat('11', 32), 'hex'),
        'Migration fixture credential');
INSERT INTO signer_sessions(
  id, tenant_id, account_id, principal_id, credential_id, auth_source,
  signer_id, token_hash, operation_key, request_hash, capability, model, app,
  expires_at, created_at
)
VALUES (
  'session_migration_fixture', 'tenant_migration_fixture',
  'account_migration_fixture', 'principal_migration_fixture',
  'credential_migration_fixture', 'credential', 'signer_migration_fixture',
  decode(repeat('22', 32), 'hex'), 'migration-operation-0001', repeat('3', 64),
  'livepeer.transcode', 'migration-model', 'migration-fixture',
  '2030-01-02T00:00:00Z', '2030-01-01T00:00:00Z'
);
COMMIT;
SQL

assert_scalar '20260909_0005|1|1' \
  "SELECT concat((SELECT version_num FROM alembic_version), '|',
    (SELECT count(*) FROM credentials WHERE id='credential_migration_fixture'), '|',
    (SELECT count(*) FROM signer_sessions WHERE id='session_migration_fixture'))"

alembic upgrade 20260909_0006
assert_scalar '20260909_0006|1|1|1' \
  "SELECT concat((SELECT version_num FROM alembic_version), '|',
    (SELECT count(*) FROM credentials WHERE id='credential_migration_fixture'), '|',
    (SELECT count(*) FROM signer_sessions WHERE id='session_migration_fixture'), '|',
    (SELECT count(*) FROM principals WHERE id='principal_system_metering'))"

psql <<'SQL'
INSERT INTO metering_worker_heartbeats(
  consumer_group, topic, signer_id, started_at, last_seen_at
) VALUES (
  'migration-fixture-group', 'migration-fixture-topic',
  'signer_migration_fixture', '2030-01-01T00:00:00Z',
  '2030-01-01T00:00:01Z'
);
INSERT INTO metering_transport_gaps(
  id, consumer_group, topic, partition, expected_offset, observed_offset,
  reason, created_at
) VALUES (
  'gap_migration_fixture', 'migration-fixture-group',
  'migration-fixture-topic', 0, 0, 2, 'retention_gap',
  '2030-01-01T00:00:02Z'
);
SQL

assert_downgrade_refused \
  20260909_0005 \
  '0006 downgrade requires empty metering state' \
  "SELECT concat((SELECT version_num FROM alembic_version), '|',
    (SELECT count(*) FROM credentials WHERE id='credential_migration_fixture'), '|',
    (SELECT count(*) FROM signer_sessions WHERE id='session_migration_fixture'), '|',
    (SELECT count(*) FROM metering_worker_heartbeats
      WHERE consumer_group='migration-fixture-group'), '|',
    (SELECT count(*) FROM metering_transport_gaps WHERE id='gap_migration_fixture'))"

alembic upgrade 20260910_0007
assert_scalar '20260910_0007|1|1|1|operations_jobs' \
  "SELECT concat((SELECT version_num FROM alembic_version), '|',
    (SELECT count(*) FROM metering_worker_heartbeats
      WHERE consumer_group='migration-fixture-group'), '|',
    (SELECT count(*) FROM metering_transport_gaps WHERE id='gap_migration_fixture'), '|',
    (SELECT count(*) FROM signer_sessions WHERE id='session_migration_fixture'), '|',
    to_regclass('operations_jobs')::text)"

psql <<'SQL'
INSERT INTO operations_jobs(
  id, kind, mode, status, initiator_id, idempotency_key, request_sha256,
  reason, parameters, result, started_at, completed_at
) VALUES (
  'operation_migration_fixture', 'projection_reconciliation', 'check',
  'succeeded', 'principal_system_metering', 'migration-fixture-operation-0001',
  repeat('4', 64), 'Prove persisted operational evidence survives migration',
  '{"scope":"migration-fixture"}'::jsonb, '{"status":"consistent"}'::jsonb,
  '2030-01-01T00:00:00Z', '2030-01-01T00:00:01Z'
);
SQL

assert_downgrade_refused \
  20260909_0006 \
  'operability downgrade requires empty operational state' \
  "SELECT concat((SELECT version_num FROM alembic_version), '|',
    (SELECT count(*) FROM operations_jobs WHERE id='operation_migration_fixture'), '|',
    (SELECT request_sha256 FROM operations_jobs WHERE id='operation_migration_fixture'), '|',
    (SELECT count(*) FROM metering_worker_heartbeats
      WHERE consumer_group='migration-fixture-group'), '|',
    (SELECT count(*) FROM metering_transport_gaps WHERE id='gap_migration_fixture'))"

alembic upgrade 20260910_0008
assert_scalar '20260910_0008|legacy|legacy|1|secret_purge_events' \
  "SELECT concat((SELECT version_num FROM alembic_version), '|',
    (SELECT key_id FROM credentials WHERE id='credential_migration_fixture'), '|',
    (SELECT key_id FROM signer_sessions WHERE id='session_migration_fixture'), '|',
    (SELECT count(*) FROM operations_jobs WHERE id='operation_migration_fixture'), '|',
    to_regclass('secret_purge_events')::text)"

psql <<'SQL'
UPDATE credentials
SET key_id='credential-key-v2'
WHERE id='credential_migration_fixture';
SQL

assert_downgrade_refused \
  20260910_0007 \
  'secret lifecycle downgrade requires legacy key ids' \
  "SELECT concat((SELECT version_num FROM alembic_version), '|',
    (SELECT key_id FROM credentials WHERE id='credential_migration_fixture'), '|',
    (SELECT key_id FROM signer_sessions WHERE id='session_migration_fixture'), '|',
    (SELECT count(*) FROM operations_jobs WHERE id='operation_migration_fixture'), '|',
    (SELECT count(*) FROM metering_worker_heartbeats
      WHERE consumer_group='migration-fixture-group'), '|',
    (SELECT count(*) FROM metering_transport_gaps WHERE id='gap_migration_fixture'))"

printf 'PostgreSQL 18 populated migration fixture matrix passed.\n'
