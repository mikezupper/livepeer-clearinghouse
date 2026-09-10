#!/bin/bash
set -Eeuo pipefail

readonly secret=/run/secrets/postgres-password
readonly identity=/run/secrets/age-identity

validate_secret() {
  local path=$1
  local optional=${2:-false}
  if [[ ! -e "$path" && "$optional" == true ]]; then
    return 0
  fi
  [[ ! -L "$path" && -f "$path" && -r "$path" ]] || {
    echo "configuration error: $path must be a readable regular non-symlink file" >&2
    exit 3
  }
  case "$(stat -c '%a' "$path")" in
    400|440|600|640) ;;
    *) echo "configuration error: $path has an unsafe file mode" >&2; exit 3 ;;
  esac
  local size
  size=$(stat -c '%s' "$path")
  (( size > 0 && size <= 65536 )) || {
    echo "configuration error: $path must contain 1..65536 bytes" >&2
    exit 3
  }
  [[ "${CLEARINGHOUSE_SECRET_OWNER_UID:-}" =~ ^[0-9]+$ ]] || {
    echo 'configuration error: CLEARINGHOUSE_SECRET_OWNER_UID must be numeric' >&2
    exit 3
  }
  local owner
  owner=$(stat -c '%u' "$path")
  [[ "$owner" == 0 || "$owner" == "$CLEARINGHOUSE_SECRET_OWNER_UID" ]] || {
    echo "configuration error: $path has an unexpected owner" >&2
    exit 3
  }
}

validate_identifier() {
  [[ "$2" =~ ^[A-Za-z0-9_]+$ ]] || {
    echo "configuration error: $1 must use letters, digits, or underscore" >&2
    exit 3
  }
}

[[ -z "${PGPASSWORD:-}" ]] || {
  echo 'configuration error: PGPASSWORD must be supplied only by the mounted file' >&2
  exit 3
}
[[ "${POSTGRES_PASSWORD_FILE:-$secret}" == "$secret" ]] || {
  echo "configuration error: POSTGRES_PASSWORD_FILE must be $secret" >&2
  exit 3
}
validate_secret "$secret"
validate_identifier POSTGRES_USER "${POSTGRES_USER:?POSTGRES_USER is required}"
validate_identifier POSTGRES_DB "${POSTGRES_DB:?POSTGRES_DB is required}"
[[ "${POSTGRES_HOST:-postgres}" =~ ^[A-Za-z0-9.-]+$ ]] || exit 3
[[ "${POSTGRES_PORT:-5432}" =~ ^[0-9]+$ ]] || exit 3

export PGPASSWORD
PGPASSWORD=$(<"$secret")
export PGHOST=${POSTGRES_HOST:-postgres}
export PGPORT=${POSTGRES_PORT:-5432}
export PGUSER=$POSTGRES_USER
export PGDATABASE=$POSTGRES_DB
export HOME=/nonexistent

if [[ "${1:-}" == restore-verify ]]; then
  validate_secret "$identity"
  chown 0:0 /tmp
  chmod 0700 /tmp
  install -o 10001 -g 10001 -m 0400 "$identity" /tmp/age-identity
  chown 10001:10001 /tmp
  [[ -d /backups && -r /backups ]] || {
    echo 'configuration error: encrypted backup volume is not readable' >&2
    exit 3
  }
else
  chown 0:0 /backups
  chmod 0700 /backups
  chown 10001:10001 /backups
fi

exec setpriv \
  --reuid=10001 --regid=10001 --clear-groups \
  --inh-caps=-all --ambient-caps=-all --bounding-set=-all \
  --no-new-privs /usr/local/lib/clearinghouse-ops/database.sh "$@"
