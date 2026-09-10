#!/bin/sh
set -eu

project=${COMPOSE_PROJECT_NAME:-}
[ "${OPS_FAULT_CONFIRM:-}" = disposable-only ] || {
  echo 'Refusing fault injection; set OPS_FAULT_CONFIRM=disposable-only' >&2
  exit 3
}
case "$project" in
  och-ops-test-*) ;;
  *) echo 'Refusing fault injection outside an och-ops-test-* Compose project' >&2; exit 3 ;;
esac
[ "${OPS_FAULT_TARGET:-}" = redpanda ] || {
  echo 'Refusing unsupported fault target' >&2
  exit 3
}
