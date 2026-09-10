#!/bin/sh
set -eu

password_file=${CLEARINGHOUSE_TEST_DATABASE_PASSWORD_FILE:-}
[ "$password_file" = /run/secrets/postgres-password ] || {
  printf '%s\n' 'test configuration: database password path must be /run/secrets/postgres-password' >&2
  exit 1
}
[ ! -L "$password_file" ] && [ -f "$password_file" ] && [ -r "$password_file" ] || {
  printf '%s\n' 'test configuration: database password must be a readable regular non-symlink file' >&2
  exit 1
}
case "$(stat -c '%a' "$password_file")" in 400|440|600|640) ;; *)
  printf '%s\n' 'test configuration: database password has an unsafe file mode' >&2
  exit 1
  ;;
esac
password=$(cat "$password_file")
export CLEARINGHOUSE_DATABASE_PASSWORD=$password
encoded_password=$(python -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["CLEARINGHOUSE_DATABASE_PASSWORD"], safe=""))')
export CLEARINGHOUSE_TEST_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${encoded_password}@postgres:5432/${POSTGRES_DB}"
unset CLEARINGHOUSE_DATABASE_PASSWORD password encoded_password CLEARINGHOUSE_TEST_DATABASE_PASSWORD_FILE
export HOME=/tmp

exec setpriv \
  "--reuid=${CLEARINGHOUSE_TEST_UID:-1000}" "--regid=${CLEARINGHOUSE_TEST_GID:-1000}" \
  --clear-groups --inh-caps=-all --ambient-caps=-all --bounding-set=-all \
  --no-new-privs "$@"
