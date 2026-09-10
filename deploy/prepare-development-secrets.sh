#!/bin/sh
set -eu

destination=${1:-tmp/runtime-secrets}
umask 077
mkdir -p "$destination"

create_random() {
  path=$destination/$1
  [ -e "$path" ] || openssl rand -hex 32 >"$path"
  chmod 600 "$path"
}

create_empty() {
  path=$destination/$1
  [ -e "$path" ] || : >"$path"
  chmod 600 "$path"
}

create_random auth-pepper
create_random credential-pepper
create_random signer-webhook-secret
create_random signer-session-pepper
postgres_password=$destination/postgres-password
[ -e "$postgres_password" ] || printf '%s\n' clearinghouse >"$postgres_password"
chmod 600 "$postgres_password"
create_empty google-client-secret
create_empty github-client-secret
create_empty operator-bootstrap-email
create_empty operator-bootstrap-secret
create_empty kafka-password

resend=$destination/resend-api-key
[ -e "$resend" ] || printf '%s\n' re_development_not_a_deliverable_key >"$resend"
chmod 600 "$resend"
