#!/bin/sh
set -eu

command=${1:-}
mode=
expect_mode=false
for argument in "$@"; do
  if [ "$expect_mode" = true ]; then
    mode=$argument
    expect_mode=false
  fi
  case "$argument" in
    --mode) expect_mode=true ;;
    --mode=*) mode=${argument#--mode=} ;;
  esac
done

validate_idempotency() {
  case "${OPS_IDEMPOTENCY_KEY:-}" in
    ''|*[!A-Za-z0-9._:-]*)
      printf '%s\n' '{"status":"refused","error":"invalid_idempotency_key"}' >&2
      exit 3
      ;;
  esac
  [ "${#OPS_IDEMPOTENCY_KEY}" -ge 16 ] && [ "${#OPS_IDEMPOTENCY_KEY}" -le 200 ] || {
    printf '%s\n' '{"status":"refused","error":"invalid_idempotency_key"}' >&2
    exit 3
  }
}

case "$command" in
  status)
    exec python -m clearinghouse.operations_worker status
    ;;
  reconcile)
    case "$mode" in
      check) ;;
      repair)
        [ "${OPS_MUTATION_CONFIRM:-}" = repair ] || {
          printf '%s\n' '{"status":"refused","error":"repair_confirmation_required"}' >&2
          exit 3
        }
        validate_idempotency
        ;;
      *) printf '%s\n' '{"status":"refused","error":"invalid_reconcile_mode"}' >&2; exit 3 ;;
    esac
    [ -n "${OPS_REASON:-}" ] || {
      printf '%s\n' '{"status":"refused","error":"reason_required"}' >&2
      exit 3
    }
    if [ -n "${OPS_IDEMPOTENCY_KEY:-}" ]; then
      exec python -m clearinghouse.operations_worker reconcile --mode "$mode" \
        --reason "$OPS_REASON" --idempotency-key "$OPS_IDEMPOTENCY_KEY"
    fi
    exec python -m clearinghouse.operations_worker reconcile --mode "$mode" --reason "$OPS_REASON"
    ;;
  retention)
    case "$mode" in
      dry-run) ;;
      apply)
        [ "${OPS_MUTATION_CONFIRM:-}" = retention ] || {
          printf '%s\n' '{"status":"refused","error":"retention_confirmation_required"}' >&2
          exit 3
        }
        validate_idempotency
        ;;
      *) printf '%s\n' '{"status":"refused","error":"invalid_retention_mode"}' >&2; exit 3 ;;
    esac
    [ -n "${OPS_REASON:-}" ] || {
      printf '%s\n' '{"status":"refused","error":"reason_required"}' >&2
      exit 3
    }
    batch_size=${OPS_BATCH_SIZE:-100}
    case "$batch_size" in
      ''|*[!0-9]*) printf '%s\n' '{"status":"refused","error":"invalid_batch_size"}' >&2; exit 3 ;;
    esac
    [ "$batch_size" -ge 1 ] && [ "$batch_size" -le 1000 ] || {
      printf '%s\n' '{"status":"refused","error":"invalid_batch_size"}' >&2
      exit 3
    }
    category=${OPS_CATEGORY:-operational_detail}
    case "$category" in auth_ephemeral|browser_sessions|operational_detail) ;;
      *) printf '%s\n' '{"status":"refused","error":"invalid_retention_category"}' >&2; exit 3 ;;
    esac
    retention_days=${OPS_RETENTION_DAYS:-30}
    case "$retention_days" in
      ''|*[!0-9]*) printf '%s\n' '{"status":"refused","error":"invalid_retention_days"}' >&2; exit 3 ;;
    esac
    [ "$retention_days" -ge 1 ] && [ "$retention_days" -le 3650 ] || {
      printf '%s\n' '{"status":"refused","error":"invalid_retention_days"}' >&2
      exit 3
    }
    if [ -n "${OPS_IDEMPOTENCY_KEY:-}" ]; then
      exec python -m clearinghouse.operations_worker retention --mode "$mode" \
        --category "$category" --reason "$OPS_REASON" --batch-size "$batch_size" \
        --retention-days "$retention_days" --idempotency-key "$OPS_IDEMPOTENCY_KEY"
    fi
    exec python -m clearinghouse.operations_worker retention --mode "$mode" \
      --category "$category" --reason "$OPS_REASON" --batch-size "$batch_size" \
      --retention-days "$retention_days"
    ;;
  backup)
    [ "${OPS_MUTATION_CONFIRM:-}" = backup-record ] || {
      printf '%s\n' '{"status":"refused","error":"backup_record_confirmation_required"}' >&2
      exit 3
    }
    validate_idempotency
    case "${OPS_BACKUP_ACTION:-}" in created|verified|restore-verified|expired) ;;
      *) printf '%s\n' '{"status":"refused","error":"invalid_backup_action"}' >&2; exit 3 ;;
    esac
    case "${OPS_BACKUP_ARTIFACT_ID:-}" in
      ''|*[!A-Za-z0-9._:-]*) printf '%s\n' '{"status":"refused","error":"invalid_artifact_id"}' >&2; exit 3 ;;
    esac
    case "${OPS_BACKUP_LOCATION_SHA256:-}:${OPS_BACKUP_CHECKSUM_SHA256:-}" in
      *[!0-9a-f:]*) printf '%s\n' '{"status":"refused","error":"invalid_backup_digest"}' >&2; exit 3 ;;
    esac
    [ "${#OPS_BACKUP_LOCATION_SHA256}" -eq 64 ] && \
      [ "${#OPS_BACKUP_CHECKSUM_SHA256}" -eq 64 ] || {
      printf '%s\n' '{"status":"refused","error":"invalid_backup_digest"}' >&2
      exit 3
    }
    case "${OPS_BACKUP_KEY_ID:-}:${OPS_BACKUP_HIGH_WATERMARK:-}" in
      :|*:|*[!A-Za-z0-9._:/-]*)
        printf '%s\n' '{"status":"refused","error":"invalid_backup_metadata"}' >&2
        exit 3
        ;;
    esac
    case "${OPS_BACKUP_RETENTION_UNTIL:-}" in
      ????-??-??T??:??:??Z) ;;
      *) printf '%s\n' '{"status":"refused","error":"invalid_backup_retention"}' >&2; exit 3 ;;
    esac
    [ -n "${OPS_REASON:-}" ] || {
      printf '%s\n' '{"status":"refused","error":"reason_required"}' >&2
      exit 3
    }
    exec python -m clearinghouse.operations_worker backup \
      --action "$OPS_BACKUP_ACTION" --artifact-id "$OPS_BACKUP_ARTIFACT_ID" \
      --backup-type logical --location-sha256 "$OPS_BACKUP_LOCATION_SHA256" \
      --checksum-sha256 "$OPS_BACKUP_CHECKSUM_SHA256" --key-id "$OPS_BACKUP_KEY_ID" \
      --high-watermark "$OPS_BACKUP_HIGH_WATERMARK" \
      --retention-until "$OPS_BACKUP_RETENTION_UNTIL" --reason "$OPS_REASON" \
      --idempotency-key "$OPS_IDEMPOTENCY_KEY"
    ;;
  rotation)
    [ "${OPS_MUTATION_CONFIRM:-}" = rotation-record ] || {
      printf '%s\n' '{"status":"refused","error":"rotation_confirmation_required"}' >&2
      exit 3
    }
    validate_idempotency
    case "${OPS_ROTATION_PURPOSE:-}" in
      auth_pepper|credential_pepper|session_pepper|backup_encryption|signer_webhook) ;;
      *) printf '%s\n' '{"status":"refused","error":"invalid_rotation_purpose"}' >&2; exit 3 ;;
    esac
    case "${OPS_ROTATION_ACTION:-}" in started|activated|retired) ;;
      *) printf '%s\n' '{"status":"refused","error":"invalid_rotation_action"}' >&2; exit 3 ;;
    esac
    case "${OPS_ROTATION_ID:-}:${OPS_ROTATION_KEY_ID:-}:${OPS_ROTATION_PRIOR_KEY_ID:-}" in
      :*|*::*|*[!A-Za-z0-9._:-]*)
        printf '%s\n' '{"status":"refused","error":"invalid_rotation_metadata"}' >&2
        exit 3
        ;;
    esac
    [ -n "${OPS_REASON:-}" ] || {
      printf '%s\n' '{"status":"refused","error":"reason_required"}' >&2
      exit 3
    }
    if [ -n "${OPS_ROTATION_PRIOR_KEY_ID:-}" ]; then
      exec python -m clearinghouse.operations_worker rotation \
        --rotation-id "$OPS_ROTATION_ID" --purpose "$OPS_ROTATION_PURPOSE" \
        --action "$OPS_ROTATION_ACTION" --key-id "$OPS_ROTATION_KEY_ID" \
        --prior-key-id "$OPS_ROTATION_PRIOR_KEY_ID" --reason "$OPS_REASON" \
        --idempotency-key "$OPS_IDEMPOTENCY_KEY"
    fi
    exec python -m clearinghouse.operations_worker rotation \
      --rotation-id "$OPS_ROTATION_ID" --purpose "$OPS_ROTATION_PURPOSE" \
      --action "$OPS_ROTATION_ACTION" --key-id "$OPS_ROTATION_KEY_ID" \
      --reason "$OPS_REASON" --idempotency-key "$OPS_IDEMPOTENCY_KEY"
    ;;
  *) printf '%s\n' '{"status":"refused","error":"unknown_command"}' >&2; exit 3 ;;
esac
