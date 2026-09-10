#!/bin/sh
set -eu

resolve_file() {
  name=$1
  expected_path=$2
  optional=${3:-false}
  file_name=${name}_FILE
  value=$(printenv "$name" 2>/dev/null || true)
  path=$(printenv "$file_name" 2>/dev/null || true)
  if [ -n "$value" ] && [ -n "$path" ]; then
    printf '%s\n' "configuration error: set either $name or $file_name, not both" >&2
    exit 1
  fi
  if [ -n "$path" ]; then
    [ "$path" = "$expected_path" ] || {
      printf '%s\n' "configuration error: $file_name must be $expected_path" >&2
      exit 1
    }
    [ ! -L "$path" ] && [ -f "$path" ] && [ -r "$path" ] || {
      printf '%s\n' "configuration error: $file_name must name a readable regular non-symlink file" >&2
      exit 1
    }
    mode=$(stat -c '%a' "$path")
    case "$mode" in 400|440|600|640) ;; *)
      printf '%s\n' "configuration error: $file_name has an unsafe file mode" >&2
      exit 1
      ;;
    esac
    size=$(stat -c '%s' "$path")
    [ "$size" -le 65536 ] || {
      printf '%s\n' "configuration error: $file_name exceeds 65536 bytes" >&2
      exit 1
    }
    case "${CLEARINGHOUSE_SECRET_OWNER_UID:-}" in ''|*[!0-9]*)
      printf '%s\n' 'configuration error: CLEARINGHOUSE_SECRET_OWNER_UID must be a numeric UID' >&2
      exit 1
      ;;
    esac
    owner=$(stat -c '%u' "$path")
    [ "$owner" -eq 0 ] || [ "$owner" -eq "$CLEARINGHOUSE_SECRET_OWNER_UID" ] || {
      printf '%s\n' "configuration error: $file_name has an unexpected owner" >&2
      exit 1
    }
    value=$(cat "$path")
    if [ -z "$value" ] && [ "$optional" = true ]; then
      unset "$name" "$file_name"
      return
    fi
    [ -n "$value" ] || {
      printf '%s\n' "configuration error: $file_name resolved to an empty value" >&2
      exit 1
    }
    export "$name=$value"
    unset "$file_name"
  fi
}

resolve_file CLEARINGHOUSE_DATABASE_URL /run/secrets/database-url true
resolve_file CLEARINGHOUSE_DATABASE_PASSWORD /run/secrets/postgres-password true
resolve_file CLEARINGHOUSE_AUTH_PEPPER /run/secrets/auth-pepper
resolve_file CLEARINGHOUSE_CREDENTIAL_PEPPER /run/secrets/credential-pepper
resolve_file CLEARINGHOUSE_AUTH_RESEND_API_KEY /run/secrets/resend-api-key
resolve_file CLEARINGHOUSE_SIGNER_WEBHOOK_SECRET /run/secrets/signer-webhook-secret
resolve_file CLEARINGHOUSE_SIGNER_SESSION_PEPPER /run/secrets/signer-session-pepper
resolve_file CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_SECRET /run/secrets/google-client-secret true
resolve_file CLEARINGHOUSE_AUTH_GITHUB_CLIENT_SECRET /run/secrets/github-client-secret true
resolve_file CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL /run/secrets/operator-bootstrap-email true
resolve_file CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET /run/secrets/operator-bootstrap-secret true

if [ -n "${CLEARINGHOUSE_DATABASE_PASSWORD:-}" ]; then
  [ -z "${CLEARINGHOUSE_DATABASE_URL:-}" ] || {
    printf '%s\n' 'configuration error: set database URL or database password file fields, not both' >&2
    exit 1
  }
  for name in CLEARINGHOUSE_DATABASE_USER CLEARINGHOUSE_DATABASE_HOST CLEARINGHOUSE_DATABASE_PORT CLEARINGHOUSE_DATABASE_NAME; do
    [ -n "$(printenv "$name" 2>/dev/null || true)" ] || {
      printf '%s\n' "configuration error: $name is required with CLEARINGHOUSE_DATABASE_PASSWORD_FILE" >&2
      exit 1
    }
  done
  case "$CLEARINGHOUSE_DATABASE_USER" in *[!A-Za-z0-9_]*)
      printf '%s\n' 'configuration error: database user and name must use letters, digits, or underscore' >&2
      exit 1
      ;;
  esac
  case "$CLEARINGHOUSE_DATABASE_NAME" in *[!A-Za-z0-9_]*)
      printf '%s\n' 'configuration error: database user and name must use letters, digits, or underscore' >&2
      exit 1
      ;;
  esac
  CLEARINGHOUSE_DATABASE_URL=$(python /app/deploy/database_url.py)
  export CLEARINGHOUSE_DATABASE_URL
  unset CLEARINGHOUSE_DATABASE_PASSWORD
fi

for name in \
  CLEARINGHOUSE_AUTH_GOOGLE_CLIENT_ID \
  CLEARINGHOUSE_AUTH_GOOGLE_REDIRECT_URI \
  CLEARINGHOUSE_AUTH_GITHUB_CLIENT_ID \
  CLEARINGHOUSE_AUTH_GITHUB_REDIRECT_URI \
  CLEARINGHOUSE_OPERATOR_BOOTSTRAP_EMAIL \
  CLEARINGHOUSE_OPERATOR_BOOTSTRAP_SECRET
do
  [ -n "$(printenv "$name" 2>/dev/null || true)" ] || unset "$name"
done

if [ "${1:-}" = --validate-only ]; then
  exit 0
fi

[ "$(id -u)" -eq 0 ] || {
  printf '%s\n' 'configuration error: backend entrypoint must start as root to read protected secret mounts' >&2
  exit 1
}
export HOME=/nonexistent

exec setpriv \
  --reuid=10001 --regid=10001 --clear-groups \
  --inh-caps=-all --ambient-caps=-all --bounding-set=-all \
  --no-new-privs "$@"
