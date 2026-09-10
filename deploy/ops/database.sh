#!/bin/bash
set -Eeuo pipefail

json_error() {
  printf '{"status":"refused","error":"%s"}\n' "$1" >&2
  exit 3
}

safe_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.age$ ]] || json_error unsafe_backup_name
}

verify_bundle() {
  local name=$1
  local artifact=$2
  local checksum=$3
  local metadata=$4
  local completion=$5
  local manifest_file=/backups/manifest.jsonl
  [[ -f "$artifact" && -f "$checksum" && -f "$metadata" && -f "$completion" ]] || \
    json_error backup_not_completed
  [[ -f "$manifest_file" ]] || json_error backup_not_in_manifest
  (cd /backups && sha256sum --check --status "$name.sha256") || json_error checksum_mismatch

  mapfile -t metadata_lines <"$metadata"
  [[ ${#metadata_lines[@]} -eq 9 ]] || json_error invalid_backup_metadata
  expected_revision=${metadata_lines[0]#revision=}
  expected_lsn=${metadata_lines[1]#source_lsn=}
  expected_key_id=${metadata_lines[2]#key_id=}
  expected_retention=${metadata_lines[3]#retention_until=}
  expected_ledger_transactions=${metadata_lines[4]#ledger_transactions=}
  expected_ledger_postings=${metadata_lines[5]#ledger_postings=}
  expected_usage_events=${metadata_lines[6]#usage_events=}
  expected_charges=${metadata_lines[7]#charges=}
  expected_tombstones=${metadata_lines[8]#lifecycle_tombstones=}
  [[ "${metadata_lines[0]}" == "revision=$expected_revision" && \
     "${metadata_lines[1]}" == "source_lsn=$expected_lsn" && \
     "${metadata_lines[2]}" == "key_id=$expected_key_id" && \
     "${metadata_lines[3]}" == "retention_until=$expected_retention" && \
     "${metadata_lines[4]}" == "ledger_transactions=$expected_ledger_transactions" && \
     "${metadata_lines[5]}" == "ledger_postings=$expected_ledger_postings" && \
     "${metadata_lines[6]}" == "usage_events=$expected_usage_events" && \
     "${metadata_lines[7]}" == "charges=$expected_charges" && \
     "${metadata_lines[8]}" == "lifecycle_tombstones=$expected_tombstones" ]] || \
    json_error invalid_backup_metadata
  [[ "$expected_revision" =~ ^[A-Za-z0-9_]+$ && \
     "$expected_lsn" =~ ^[0-9A-F]+/[0-9A-F]+$ && \
     "$expected_key_id" =~ ^[A-Za-z0-9._:-]{1,128}$ && \
     "$expected_retention" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || \
    json_error invalid_backup_metadata
  for count in "$expected_ledger_transactions" "$expected_ledger_postings" \
    "$expected_usage_events" "$expected_charges" "$expected_tombstones"; do
    [[ "$count" =~ ^[0-9]+$ ]] || json_error invalid_backup_metadata
  done

  [[ $(grep -Fc "\"artifact\":\"$name\"" "$manifest_file") -eq 1 ]] || \
    json_error invalid_backup_manifest
  manifest_line=$(grep -F "\"artifact\":\"$name\"" "$manifest_file")
  created_at=$(sed -n 's/.*"created_at":"\([^"]*\)".*/\1/p' <<<"$manifest_line")
  bytes=$(sed -n 's/.*"bytes":\([0-9]*\).*/\1/p' <<<"$manifest_line")
  postgres_version=$(sed -n 's/.*"postgres":"\([^"]*\)".*/\1/p' <<<"$manifest_line")
  age_version=$(sed -n 's/.*"age":"\([^"]*\)".*/\1/p' <<<"$manifest_line")
  digest=$(cut -d ' ' -f 1 "$checksum")
  [[ "$digest" =~ ^[0-9a-f]{64}$ && "$created_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ && \
     "$bytes" =~ ^[1-9][0-9]*$ && "$postgres_version" =~ ^[A-Za-z0-9.\ \(\)-]+$ && \
     "$age_version" =~ ^[A-Za-z0-9.\ -]+$ ]] || json_error invalid_backup_manifest
  expected_manifest=$(printf \
    '{"artifact":"%s","status":"completed","created_at":"%s","retention_until":"%s","bytes":%s,"sha256":"%s","key_id":"%s","alembic_revision":"%s","source_lsn":"%s","postgres":"%s","age":"%s"}' \
    "$name" "$created_at" "$expected_retention" "$bytes" "$digest" "$expected_key_id" \
    "$expected_revision" "$expected_lsn" "$postgres_version" "$age_version")
  [[ "$manifest_line" == "$expected_manifest" ]] || json_error invalid_backup_manifest

  mapfile -t completion_lines <"$completion"
  [[ ${#completion_lines[@]} -eq 3 ]] || json_error invalid_backup_completion
  metadata_digest=$(sha256sum "$metadata" | cut -d ' ' -f 1)
  manifest_digest=$(printf '%s' "$manifest_line" | sha256sum | cut -d ' ' -f 1)
  [[ "${completion_lines[0]}" == "archive_sha256=$digest" && \
     "${completion_lines[1]}" == "metadata_sha256=$metadata_digest" && \
     "${completion_lines[2]}" == "manifest_sha256=$manifest_digest" ]] || \
    json_error completion_checksum_mismatch
}

read_snapshot_metadata() {
  local snapshot=${1:-}
  local prefix=
  local suffix=
  if [[ -n "$snapshot" ]]; then
    prefix="begin transaction isolation level serializable read only;
      set transaction snapshot '$snapshot';"
    suffix="commit;"
  fi
  psql -XqAtF ' ' --set ON_ERROR_STOP=1 -c \
    "$prefix
     select version_num, pg_current_wal_lsn(),
       (select count(*) from ledger_transactions),
       (select count(*) from ledger_postings),
       (select count(*) from usage_events),
       (select count(*) from charges),
       (select count(*) from lifecycle_tombstones)
     from alembic_version;
     $suffix"
}

case "${1:-}" in
  status)
    if pg_isready -q; then
      size=$(psql -XAt --set ON_ERROR_STOP=1 -c \
        "select pg_database_size(current_database())")
      printf '{"status":"ready","database_bytes":%s}\n' "$size"
    else
      printf '{"status":"degraded","database":"unavailable"}\n'
      exit 4
    fi
    ;;
  capacity)
    pg_isready -q || { printf '{"status":"degraded","database":"unavailable"}\n'; exit 4; }
    read -r database_bytes connections max_connections < <(
      psql -XAtF ' ' --set ON_ERROR_STOP=1 -c \
        "select pg_database_size(current_database()), (select count(*) from pg_stat_activity), current_setting('max_connections')"
    )
    printf '{"status":"ready","database_bytes":%s,"connections":%s,"max_connections":%s}\n' \
      "$database_bytes" "$connections" "$max_connections"
    ;;
  inspect)
    name=${2:-${OPS_BACKUP_NAME:-}}
    safe_name "$name"
    artifact="/backups/$name"
    checksum="$artifact.sha256"
    metadata="$artifact.metadata"
    completion="$artifact.complete"
    verify_bundle "$name" "$artifact" "$checksum" "$metadata" "$completion"
    key_id=$expected_key_id
    source_lsn=$expected_lsn
    retention=$expected_retention
    printf '{"status":"completed","artifact":"%s","sha256":"%s","key_id":"%s","source_lsn":"%s","retention_until":"%s"}\n' \
      "$name" "$digest" "$key_id" "$source_lsn" "$retention"
    ;;
  backup)
    [[ "${OPS_BACKUP_CONFIRM:-}" == backup ]] || json_error backup_confirmation_required
    [[ "${AGE_RECIPIENT:-}" =~ ^age1[0-9a-z]+$ ]] || json_error invalid_age_recipient
    [[ "${OPS_BACKUP_KEY_ID:-}" =~ ^[A-Za-z0-9._:-]{1,128}$ ]] || \
      json_error invalid_backup_key_id
    [[ "${OPS_BACKUP_RETENTION_UNTIL:-}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || \
      json_error invalid_backup_retention
    name=${2:-${OPS_BACKUP_NAME:-clearinghouse-$(date -u +%Y%m%dT%H%M%SZ).dump.age}}
    safe_name "$name"
    artifact="/backups/$name"
    checksum="$artifact.sha256"
    metadata="$artifact.metadata"
    completion="$artifact.complete"
    lock="/backups/.${name}.lock"
    mkdir "$lock" 2>/dev/null || json_error backup_locked_or_exists
    trap 'rm -rf -- "$lock"' EXIT
    [[ ! -e "$artifact" && ! -e "$checksum" && ! -e "$metadata" && ! -e "$completion" ]] || \
      json_error backup_already_exists
    temporary="$lock/archive.tmp"
    checksum_temporary="$lock/checksum.tmp"
    metadata_temporary="$lock/metadata.tmp"
    completion_temporary="$lock/complete.tmp"
    snapshot_output="$lock/snapshot"
    snapshot_commands="$lock/commands"
    mkfifo "$snapshot_commands"
    exporter_pid=
    cleanup_backup() {
      if [[ -n "$exporter_pid" ]] && kill -0 "$exporter_pid" 2>/dev/null; then
        printf 'rollback;\n' >&8 2>/dev/null || true
        exec 8>&-
        wait "$exporter_pid" 2>/dev/null || true
      fi
      rm -rf -- "$lock"
    }
    trap cleanup_backup EXIT
    psql -XqAt --set ON_ERROR_STOP=1 <"$snapshot_commands" >"$snapshot_output" &
    exporter_pid=$!
    exec 8>"$snapshot_commands"
    printf '%s\n' 'begin transaction isolation level serializable read only deferrable;' \
      'select pg_export_snapshot();' >&8
    snapshot=
    for _ in {1..100}; do
      candidate=$(sed -n '1p' "$snapshot_output")
      if [[ "$candidate" =~ ^[0-9]+-[0-9A-F]+-[0-9]+$ ]]; then
        snapshot=$candidate
        break
      fi
      kill -0 "$exporter_pid" 2>/dev/null || json_error snapshot_export_failed
      sleep 0.05
    done
    [[ "$snapshot" =~ ^[0-9]+-[0-9A-F]+-[0-9]+$ ]] || json_error snapshot_export_failed
    read -r revision lsn ledger_transactions ledger_postings usage_events charges tombstones \
      < <(read_snapshot_metadata "$snapshot")
    [[ "$revision" =~ ^[A-Za-z0-9_]+$ && "$lsn" =~ ^[0-9A-F]+/[0-9A-F]+$ ]] || \
      json_error invalid_database_metadata
    created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    PGOPTIONS='-c default_transaction_isolation=serializable' \
      pg_dump --format=custom --compress=zstd:9 \
      --snapshot="$snapshot" --no-owner --no-acl \
      | age --recipient "$AGE_RECIPIENT" >"$temporary"
    printf 'commit;\n' >&8
    exec 8>&-
    wait "$exporter_pid"
    exporter_pid=
    chmod 0600 "$temporary"
    digest=$(sha256sum "$temporary" | cut -d ' ' -f 1)
    bytes=$(stat -c '%s' "$temporary")
    printf '%s  %s\n' "$digest" "$name" >"$checksum_temporary"
    printf '%s\n' \
      "revision=$revision" "source_lsn=$lsn" \
      "key_id=$OPS_BACKUP_KEY_ID" "retention_until=$OPS_BACKUP_RETENTION_UNTIL" \
      "ledger_transactions=$ledger_transactions" "ledger_postings=$ledger_postings" \
      "usage_events=$usage_events" "charges=$charges" "lifecycle_tombstones=$tombstones" \
      >"$metadata_temporary"
    chmod 0600 "$checksum_temporary" "$metadata_temporary"
    pg_version=$(pg_dump --version | sed 's/[^A-Za-z0-9. ()-]//g')
    age_version=$(age --version | sed 's/[^A-Za-z0-9. -]//g')
    manifest=$(printf \
      '{"artifact":"%s","status":"completed","created_at":"%s","retention_until":"%s","bytes":%s,"sha256":"%s","key_id":"%s","alembic_revision":"%s","source_lsn":"%s","postgres":"%s","age":"%s"}' \
      "$name" "$created_at" "$OPS_BACKUP_RETENTION_UNTIL" "$bytes" "$digest" "$OPS_BACKUP_KEY_ID" "$revision" "$lsn" \
      "$pg_version" "$age_version")
    metadata_digest=$(sha256sum "$metadata_temporary" | cut -d ' ' -f 1)
    manifest_digest=$(printf '%s' "$manifest" | sha256sum | cut -d ' ' -f 1)
    printf '%s\n' "archive_sha256=$digest" "metadata_sha256=$metadata_digest" \
      "manifest_sha256=$manifest_digest" >"$completion_temporary"
    chmod 0600 "$completion_temporary"
    sync -f "$temporary" "$checksum_temporary" "$metadata_temporary" "$completion_temporary"
    ln "$temporary" "$artifact" || json_error backup_already_exists
    rm "$temporary"
    ln "$checksum_temporary" "$checksum" || json_error backup_sidecar_exists
    rm "$checksum_temporary"
    ln "$metadata_temporary" "$metadata" || json_error backup_sidecar_exists
    rm "$metadata_temporary"
    sync -f /backups
    (flock -x 9; printf '%s\n' "$manifest" >&9; sync -f /backups/manifest.jsonl) \
      9>>/backups/manifest.jsonl
    ln "$completion_temporary" "$completion" || json_error backup_completion_exists
    rm "$completion_temporary"
    sync -f /backups
    cleanup_backup
    trap - EXIT
    printf '%s\n' "$manifest"
    ;;
  restore-verify)
    [[ "${OPS_RESTORE_CONFIRM:-}" == isolated-restore ]] || json_error restore_confirmation_required
    [[ "${OPS_RESTORE_ISOLATED:-}" == true ]] || json_error isolated_database_required
    name=${2:-${OPS_BACKUP_NAME:-}}
    safe_name "$name"
    artifact="/backups/$name"
    checksum="$artifact.sha256"
    metadata="$artifact.metadata"
    completion="$artifact.complete"
    [[ "$(psql -XAt --set ON_ERROR_STOP=1 -c \
      "select count(*) from information_schema.tables where table_schema='public'")" == 0 ]] || \
      json_error restore_target_not_empty
    verify_bundle "$name" "$artifact" "$checksum" "$metadata" "$completion"
    age --decrypt --identity /tmp/age-identity "/backups/$name" \
      | pg_restore --single-transaction --exit-on-error --no-owner --no-acl --dbname "$PGDATABASE"
    tables=$(psql -XAt --set ON_ERROR_STOP=1 -c \
      "select count(*) from information_schema.tables where table_schema='public'")
    [[ "$tables" =~ ^[0-9]+$ && "$tables" -gt 0 ]] || json_error restored_schema_empty
    read -r revision _ ledger_transactions ledger_postings usage_events charges tombstones \
      < <(read_snapshot_metadata)
    expected_counts="$expected_ledger_transactions $expected_ledger_postings $expected_usage_events $expected_charges $expected_tombstones"
    actual_counts="$ledger_transactions $ledger_postings $usage_events $charges $tombstones"
    [[ "$revision" == "$expected_revision" && "$actual_counts" == "$expected_counts" ]] || \
      json_error restored_snapshot_mismatch
    drift=$(psql -XAt --set ON_ERROR_STOP=1 -c \
      "select
       (select count(*) from (
         select transaction_id, unit from ledger_postings
         group by transaction_id, unit having sum(amount)<>0 or count(*)<2
       ) unbalanced)
       + case when (select open_exposure from global_exposure where singleton) <>
         coalesce((select sum(available+pending) from leases),0) then 1 else 0 end
       + (select count(*) from account_exposures e where e.open_lease_exposure <>
         coalesce((select sum(l.available+l.pending) from leases l
                   where l.account_id=e.account_id),0))")
    [[ "$drift" == 0 ]] || json_error restored_invariant_drift
    digest=$(cut -d ' ' -f 1 "$checksum")
    printf '{"status":"verified","artifact":"%s","tables":%s,"revision":"%s","sha256":"%s","key_id":"%s","source_lsn":"%s","retention_until":"%s"}\n' \
      "$name" "$tables" "$revision" "$digest" "$expected_key_id" "$expected_lsn" \
      "$expected_retention"
    ;;
  *) json_error unknown_command ;;
esac
