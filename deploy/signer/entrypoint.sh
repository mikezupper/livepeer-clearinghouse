#!/bin/sh
# No shell-sourcing .env, command echoing, plaintext key creation, or CLI secrets.
set -eu

fail() { printf '%s\n' "signer configuration: $1" >&2; exit 1; }
case "${CLEARINGHOUSE_SECRET_OWNER_UID:-1000}" in ''|*[!0-9]*) fail 'CLEARINGHOUSE_SECRET_OWNER_UID must be numeric' ;; esac
case "${SIGNER_MODE:-}" in
  evaluation|production) ;;
  test) fail 'test mode uses contract fixtures; real signer startup requires evaluation or production' ;;
  *) fail 'set SIGNER_MODE to evaluation or production' ;;
esac
case "${SIGNER_NETWORK:-}" in
  arbitrum-one-mainnet)
    [ "${SIGNER_CHAIN_ID:-}" = 42161 ] || fail 'arbitrum-one-mainnet requires SIGNER_CHAIN_ID=42161'
    [ "${SIGNER_CONTROLLER:-}" = 0xD8E8328501E9645d16Cf49539efC04f734606ee4 ] || fail 'set SIGNER_CONTROLLER to the pinned Arbitrum controller; see docs/operations/signer.md'
    ;;
  custom)
    [ "$SIGNER_MODE" = evaluation ] || fail 'production requires arbitrum-one-mainnet'
    ;;
  *) fail 'set SIGNER_NETWORK to arbitrum-one-mainnet or custom (evaluation only)' ;;
esac
case "${SIGNER_CHAIN_ID:-}" in ''|0|*[!0-9]*) fail 'SIGNER_CHAIN_ID must be a positive decimal integer' ;; esac
for value in "${SIGNER_ETH_ADDR:-}" "${SIGNER_CONTROLLER:-}"; do
  [ "${#value}" = 42 ] || fail 'SIGNER_ETH_ADDR and SIGNER_CONTROLLER must be Ethereum addresses'
  case "$value" in 0x*[!0-9a-fA-F]*|0x0000000000000000000000000000000000000000) fail 'address must contain nonzero hex bytes' ;; 0x*) ;; *) fail 'address must begin with 0x' ;; esac
done
case "${ETH_RPC_URL:-}" in https://?*|http://?*) ;; *) fail 'ETH_RPC_URL must be an HTTP(S) RPC endpoint' ;; esac
case "$ETH_RPC_URL" in *\#*|*@*) fail 'ETH_RPC_URL must not contain userinfo or a fragment' ;; esac
printf '%s\n' 'signer configuration warning: pinned go-livepeer may print the complete credential-bearing ETH_RPC_URL in logs' >&2
case "${REMOTE_SIGNER_WEBHOOK_URL:-}" in
  https://?*/v1/compat/go-livepeer/authorize|http://?*/v1/compat/go-livepeer/authorize) ;;
  *) fail 'REMOTE_SIGNER_WEBHOOK_URL must target /v1/compat/go-livepeer/authorize' ;;
esac
case "$REMOTE_SIGNER_WEBHOOK_URL" in *\?*|*\#*|*@*) fail 'webhook URL must not contain credentials, query, or fragment; use WEBHOOK_SECRET' ;; esac
if [ -n "${WEBHOOK_SECRET:-}" ] && [ -n "${WEBHOOK_SECRET_FILE:-}" ]; then
  fail 'set either WEBHOOK_SECRET or WEBHOOK_SECRET_FILE, not both'
fi
if [ -n "${WEBHOOK_SECRET_FILE:-}" ]; then
  [ "$WEBHOOK_SECRET_FILE" = /run/secrets/signer-webhook-secret ] || fail 'WEBHOOK_SECRET_FILE must be /run/secrets/signer-webhook-secret'
  [ ! -L "$WEBHOOK_SECRET_FILE" ] && [ -f "$WEBHOOK_SECRET_FILE" ] && [ -r "$WEBHOOK_SECRET_FILE" ] && [ -s "$WEBHOOK_SECRET_FILE" ] || fail 'WEBHOOK_SECRET_FILE must name a readable regular non-symlink file'
  case "$(stat -c '%a' "$WEBHOOK_SECRET_FILE")" in 400|440|600|640) ;; *) fail 'WEBHOOK_SECRET_FILE has an unsafe file mode' ;; esac
  [ "$(stat -c '%s' "$WEBHOOK_SECRET_FILE")" -le 65536 ] || fail 'WEBHOOK_SECRET_FILE exceeds 65536 bytes'
  owner=$(stat -c '%u' "$WEBHOOK_SECRET_FILE")
  [ "$owner" -eq 0 ] || [ "$owner" -eq "${CLEARINGHOUSE_SECRET_OWNER_UID:-1000}" ] || fail 'WEBHOOK_SECRET_FILE has an unexpected owner'
  WEBHOOK_SECRET=$(cat "$WEBHOOK_SECRET_FILE")
  unset WEBHOOK_SECRET_FILE
else
  WEBHOOK_SECRET=${WEBHOOK_SECRET:-}
fi
[ "${#WEBHOOK_SECRET}" -ge 32 ] || fail 'WEBHOOK_SECRET requires at least 32 URL-safe characters'
case "$WEBHOOK_SECRET" in *[!A-Za-z0-9_-]*) fail 'WEBHOOK_SECRET must use URL-safe characters (no commas, spaces, or header delimiters)' ;; esac
case "${SIGNER_PORT:-8935}" in ''|*[!0-9]*) fail 'SIGNER_PORT must be an integer' ;; esac
SIGNER_PORT=${SIGNER_PORT:-8935}
[ "$SIGNER_PORT" -ge 1024 ] && [ "$SIGNER_PORT" -le 65535 ] && [ "$SIGNER_PORT" -ne 4935 ] || fail 'SIGNER_PORT must be 1024..65535 and distinct from private admin port 4935'
case "${KAFKA_BROKERS:-}" in ''|:*|*:*:*|*[!A-Za-z0-9.:-]*) fail 'KAFKA_BROKERS must be one host:port (the pinned signer accepts one bootstrap address)' ;; *:*) ;; *) fail 'KAFKA_BROKERS requires host:port' ;; esac
kafka_port=${KAFKA_BROKERS##*:}
case "$kafka_port" in ''|*[!0-9]*) fail 'KAFKA_BROKERS port must be 1..65535' ;; esac
[ "$kafka_port" -ge 1 ] && [ "$kafka_port" -le 65535 ] || fail 'KAFKA_BROKERS port must be 1..65535'
case "${KAFKA_GATEWAY_TOPIC:-}" in ''|.|..|*[!A-Za-z0-9._-]*) fail 'set KAFKA_GATEWAY_TOPIC to a valid topic name' ;; esac
[ "${#KAFKA_GATEWAY_TOPIC}" -le 249 ] || fail 'KAFKA_GATEWAY_TOPIC must not exceed 249 characters'
if [ -n "${LP_KAFKAPASSWORD_FILE:-}" ]; then
  [ -z "${LP_KAFKAPASSWORD:-}" ] || fail 'set either LP_KAFKAPASSWORD or LP_KAFKAPASSWORD_FILE, not both'
  [ "$LP_KAFKAPASSWORD_FILE" = /run/secrets/kafka-password ] || fail 'LP_KAFKAPASSWORD_FILE must be /run/secrets/kafka-password'
  [ ! -L "$LP_KAFKAPASSWORD_FILE" ] && [ -f "$LP_KAFKAPASSWORD_FILE" ] && [ -r "$LP_KAFKAPASSWORD_FILE" ] || fail 'LP_KAFKAPASSWORD_FILE must name a readable regular non-symlink file'
  case "$(stat -c '%a' "$LP_KAFKAPASSWORD_FILE")" in 400|440|600|640) ;; *) fail 'LP_KAFKAPASSWORD_FILE has an unsafe file mode' ;; esac
  [ "$(stat -c '%s' "$LP_KAFKAPASSWORD_FILE")" -le 65536 ] || fail 'LP_KAFKAPASSWORD_FILE exceeds 65536 bytes'
  owner=$(stat -c '%u' "$LP_KAFKAPASSWORD_FILE")
  [ "$owner" -eq 0 ] || [ "$owner" -eq "${CLEARINGHOUSE_SECRET_OWNER_UID:-1000}" ] || fail 'LP_KAFKAPASSWORD_FILE has an unexpected owner'
  LP_KAFKAPASSWORD=$(cat "$LP_KAFKAPASSWORD_FILE")
  export LP_KAFKAPASSWORD
  unset LP_KAFKAPASSWORD_FILE
fi
[ -z "${LP_KAFKAUSER:-}" ] && [ -z "${LP_KAFKAPASSWORD:-}" ] || {
  [ -n "${LP_KAFKAUSER:-}" ] && [ -n "${LP_KAFKAPASSWORD:-}" ] || fail 'configure both LP_KAFKAUSER and LP_KAFKAPASSWORD or neither'
}
case "${SIGNER_REMOTE_DISCOVERY:-true}" in true|false) ;; *) fail 'SIGNER_REMOTE_DISCOVERY must be true or false' ;; esac
if [ -n "${SIGNER_ORCH_ADDR:-}" ]; then
  [ "${SIGNER_REMOTE_DISCOVERY:-true}" = true ] || fail 'SIGNER_ORCH_ADDR requires SIGNER_REMOTE_DISCOVERY=true'
  [ "${#SIGNER_ORCH_ADDR}" -le 8192 ] || fail 'SIGNER_ORCH_ADDR must not exceed 8192 characters'
  case "$SIGNER_ORCH_ADDR" in ,*|*,|*,,*) fail 'SIGNER_ORCH_ADDR must not contain empty entries' ;; esac
  old_ifs=$IFS
  IFS=,
  set -f
  # Intentional splitting: each entry is validated before the original string is
  # passed as one quoted argument to go-livepeer.
  endpoint_count=0
  for endpoint in $SIGNER_ORCH_ADDR; do
    endpoint_count=$((endpoint_count + 1))
    [ "$endpoint_count" -le 256 ] || fail 'SIGNER_ORCH_ADDR must not contain more than 256 service addresses'
    case "$endpoint" in
      *[[:space:]]*|*@*|*\?*|*\#*) fail 'SIGNER_ORCH_ADDR entries must not contain whitespace, credentials, queries, or fragments' ;;
    esac
    case "$endpoint" in
      https://*) authority=${endpoint#https://} ;;
      http://*)
        [ "$SIGNER_MODE" != production ] || fail 'production SIGNER_ORCH_ADDR entries must use HTTPS'
        authority=${endpoint#http://}
        ;;
      *://*) fail 'SIGNER_ORCH_ADDR entries must use HTTP or HTTPS' ;;
      *) authority=$endpoint ;;
    esac
    case "$authority" in ''|*/*|*[!A-Za-z0-9.:-]*) fail 'SIGNER_ORCH_ADDR entries must be DNS or IPv4 service addresses with explicit ports' ;; esac
    host=${authority%:*}
    port=${authority##*:}
    [ -n "$host" ] && [ "$host" != "$authority" ] || fail 'SIGNER_ORCH_ADDR entries require an explicit port'
    case "$host" in *:*) fail 'SIGNER_ORCH_ADDR entries must use DNS names or IPv4 addresses' ;; esac
    case "$port" in ''|*[!0-9]*) fail 'SIGNER_ORCH_ADDR ports must be integers' ;; esac
    [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || fail 'SIGNER_ORCH_ADDR ports must be 1..65535'
  done
  set +f
  IFS=$old_ifs
fi
SIGNER_ETH_KEYSTORE_PATH=${SIGNER_ETH_KEYSTORE_PATH:-/run/secrets/signer-keystore.json}
SIGNER_PASSWORD_FILE=${SIGNER_PASSWORD_FILE:-/run/secrets/signer-password}
[ "$SIGNER_ETH_KEYSTORE_PATH" = /run/secrets/signer-keystore.json ] || fail 'SIGNER_ETH_KEYSTORE_PATH must be /run/secrets/signer-keystore.json'
[ "$SIGNER_PASSWORD_FILE" = /run/secrets/signer-password ] || fail 'SIGNER_PASSWORD_FILE must be /run/secrets/signer-password'
[ ! -L "$SIGNER_ETH_KEYSTORE_PATH" ] && [ -f "$SIGNER_ETH_KEYSTORE_PATH" ] && [ -r "$SIGNER_ETH_KEYSTORE_PATH" ] && [ -s "$SIGNER_ETH_KEYSTORE_PATH" ] || fail 'mount one readable regular non-symlink encrypted keyfile at SIGNER_ETH_KEYSTORE_PATH'
[ ! -L "$SIGNER_PASSWORD_FILE" ] && [ -f "$SIGNER_PASSWORD_FILE" ] && [ -r "$SIGNER_PASSWORD_FILE" ] && [ -s "$SIGNER_PASSWORD_FILE" ] || fail 'mount a readable regular non-symlink password file at SIGNER_PASSWORD_FILE'
for custody_file in "$SIGNER_ETH_KEYSTORE_PATH" "$SIGNER_PASSWORD_FILE"; do
  case "$(stat -c '%a' "$custody_file")" in 400|440|600|640) ;; *) fail 'signer custody file has an unsafe file mode' ;; esac
  [ "$(stat -c '%s' "$custody_file")" -le 1048576 ] || fail 'signer custody file exceeds 1048576 bytes'
  owner=$(stat -c '%u' "$custody_file")
  [ "$owner" -eq 0 ] || [ "$owner" -eq "${CLEARINGHOUSE_SECRET_OWNER_UID:-1000}" ] || fail 'signer custody file has an unexpected owner'
done
# Reject accidental plaintext files; full JSON/address validation is preflight.py.
grep -qi '"ciphertext"' "$SIGNER_ETH_KEYSTORE_PATH" || fail 'keystore must be encrypted Ethereum V3 JSON; run preflight.py'
grep -q '[^[:space:]]' "$SIGNER_PASSWORD_FILE" || fail 'empty/whitespace keystore passwords are not supported'

SIGNER_DATA_DIR=${SIGNER_DATA_DIR:-/data}
[ -d "$SIGNER_DATA_DIR" ] && [ -w "$SIGNER_DATA_DIR" ] || fail 'mount a writable SIGNER_DATA_DIR owned by UID/GID 10001'
case "${1:-}" in --validate-only) printf '%s\n' 'signer configuration valid; run preflight before enabling traffic'; exit 0 ;; '') ;; *) fail 'entrypoint does not accept extra flags; configure named environment settings' ;; esac

# Source files remain operator-owned mode 0600. Copy only the signer custody
# inputs into this container's tmpfs before permanently dropping privileges.
runtime_secret_dir=/runtime-secrets
[ "$(id -u)" -eq 0 ] || fail 'entrypoint must start as root to read protected secret mounts'
install -m 0400 "$SIGNER_ETH_KEYSTORE_PATH" "$runtime_secret_dir/signer-keystore.json"
install -m 0400 "$SIGNER_PASSWORD_FILE" "$runtime_secret_dir/signer-password"
chown 10001:10001 "$runtime_secret_dir/signer-keystore.json" "$runtime_secret_dir/signer-password"
SIGNER_ETH_KEYSTORE_PATH=$runtime_secret_dir/signer-keystore.json
SIGNER_ETH_KEYSTORE_DIR=$runtime_secret_dir
SIGNER_PASSWORD_FILE=$runtime_secret_dir/signer-password

# Native ff parser uppercases the flag name, without inserting camel-case underscores.
# Explicit non-secret flags override any ambient LP_* settings for mandatory controls.
export LP_ETHURL="$ETH_RPC_URL"
export LP_REMOTESIGNERWEBHOOKHEADERS="Authorization:Bearer $WEBHOOK_SECRET"
export HOME=/nonexistent
unset WEBHOOK_SECRET ETH_RPC_URL
exec setpriv \
  --reuid=10001 --regid=10001 --clear-groups \
  --inh-caps=-chown,-dac_override,-setpcap,-setgid,-setuid \
  --ambient-caps=-chown,-dac_override,-setpcap,-setgid,-setuid \
  --bounding-set=-chown,-dac_override,-setpcap,-setgid,-setuid \
  --no-new-privs /usr/local/bin/livepeer \
  -remoteSigner=true -remoteSignerAllowNoAuth=false -monitor=true \
  "-network=$SIGNER_NETWORK" "-ethController=$SIGNER_CONTROLLER" \
  "-ethAcctAddr=$SIGNER_ETH_ADDR" "-ethKeystorePath=$SIGNER_ETH_KEYSTORE_DIR" \
  "-ethPassword=$SIGNER_PASSWORD_FILE" "-datadir=$SIGNER_DATA_DIR" \
  "-httpAddr=0.0.0.0:$SIGNER_PORT" -cliAddr=127.0.0.1:4935 \
  "-remoteSignerWebhookUrl=$REMOTE_SIGNER_WEBHOOK_URL" \
  "-remoteDiscovery=${SIGNER_REMOTE_DISCOVERY:-true}" \
  "-orchAddr=${SIGNER_ORCH_ADDR:-}" \
  "-kafkaBootstrapServers=$KAFKA_BROKERS" "-kafkaGatewayTopic=$KAFKA_GATEWAY_TOPIC" -v=3
