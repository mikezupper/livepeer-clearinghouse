# Remote-signer bootstrap and operations

The reference signer is unmodified go-livepeer. Its wrapper validates operator
configuration and starts the upstream binary with mandatory authorization and
Kafka monitoring. A clean checkout can run contract tests without money or
keys. Signing on a Livepeer network additionally requires an operator-owned
encrypted keystore, working RPC, gas, and funded TicketBroker deposit/reserve.
No startup script creates a wallet, stores plaintext keys, sends a transaction,
or funds the signer automatically.

## Supported configurations

| Mode | Chain and custody | What can be verified |
| --- | --- | --- |
| Test | Deterministic contract fixtures; no key, RPC, or real signer process | Authorization wire compatibility, reservation and settlement invariants, Kafka normalization |
| Local evaluation | Explicit Arbitrum One configuration, or `custom` with a separately deployed development chain and Livepeer contracts; disposable encrypted key supplied by operator | Real signer bootstrap and controlled signing with operator-selected funds |
| Production | Arbitrum One, encrypted operator keystore and separate password mount, private RPC/broker/API networking | Funded signing after all readiness and conformance checks pass |

`SIGNER_MODE=test` deliberately refuses to launch the funded upstream signer.
An empty keystore is not a test mode. `custom` does not create a development
chain or contracts. Do not select the pinned binary's deprecated Rinkeby modes.

## Image and platform

The source contract is pinned to commit
`e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6`:

- OCI index: `livepeer/go-livepeer@sha256:ea7a9434e66ae328711c3324ca58536dc7e85da0ab43c02a6550d58ac7f9d8b6`.
- Linux amd64 image: `sha256:d6bbae2b0a1e0d4fd508ddbac0e8bad4466c0c302163af8aeeb084160319d009`.
- Index's other descriptor is a provenance attestation, not an arm64 image.

[deploy/signer/Dockerfile](../../deploy/signer/Dockerfile) adds only the wrapper
and a writable data directory. The upstream executable and CUDA-linked runtime
libraries remain intact. No GPU device is required for remote ticket signing.
Use `platform: linux/amd64`; an ARM host needs explicit emulation or a separately
reviewed upstream build. Image upgrades require rerunning signer contract tests,
not merely updating a mutable tag.

## Configuration and mounts

Merge [the signer environment example](../../deploy/signer/.env.example) into
local `.env`; Compose must pass only relevant values to each service. The
preflight accepts literal `KEY=value`, single/double quotes, and full-line
comments. It performs no shell execution, interpolation, or inline comments.
Do not shell-source arbitrary `.env` contents.

| Setting | Requirement |
| --- | --- |
| `SIGNER_MODE` | `evaluation` or `production` for a real signer |
| `SIGNER_NETWORK` | `arbitrum-one-mainnet`, or `custom` for evaluation |
| `SIGNER_CHAIN_ID` | `42161` for Arbitrum One; explicit positive ID for custom chains |
| `SIGNER_CONTROLLER` | `0xD8E8328501E9645d16Cf49539efC04f734606ee4` for the pinned Arbitrum configuration; explicit deployed contract for custom chains |
| `ETH_RPC_URL` | HTTP(S) RPC; custom provider paths and query credentials are accepted, while URL userinfo and fragments remain invalid |
| `SIGNER_ETH_ADDR` | Nonzero address matching the V3 encrypted keyfile |
| `SIGNER_KEYSTORE_HOST_FILE` | Existing host encrypted Ethereum V3 JSON file; never a plaintext private key |
| `SIGNER_PASSWORD_HOST_FILE` | Separate nonempty password file, readable by runtime UID/GID; mode `0400`, `0440`, `0600`, or `0640` |
| `SIGNER_ETH_KEYSTORE_PATH` | Container keyfile path, normally `/run/secrets/signer-keystore.json` |
| `SIGNER_PASSWORD_FILE` | Container password path, normally `/run/secrets/signer-password` |
| `SIGNER_DATA_DIR` | Writable persistent directory `/data`, owned by UID/GID `10001` |
| `SIGNER_PORT` / `SIGNER_HOST_PORT` | Container signing port `8935`; optional loopback host port `18935` |
| `SIGNER_REMOTE_DISCOVERY` | `true` to expose upstream `/discover-orchestrators`; `false` when every gateway receives discovery elsewhere |
| `SIGNER_ORCH_ADDR` | Optional comma-separated static orchestrator service addresses (for example, `https://orch-a.example:8935,https://orch-b.example:8935`); requires `SIGNER_REMOTE_DISCOVERY=true`, explicit ports, and HTTPS in production |
| `REMOTE_SIGNER_WEBHOOK_URL` | `/v1/compat/go-livepeer/authorize` on the private clearinghouse API |
| `WEBHOOK_SECRET` | Independently generated shared service credential, at least 32 URL-safe characters; must match API configuration |
| `KAFKA_BROKERS` | One reachable `hostname:port`, normally `redpanda:9092`; this upstream constructor accepts one bootstrap address |
| `KAFKA_GATEWAY_TOPIC` | Provisioned topic `livepeer-gateway-events` |
| `LP_KAFKAUSER` / `LP_KAFKAPASSWORD_HOST_FILE` | Both absent for private local Redpanda, or a username plus protected password file for upstream's SASL/PLAIN + TLS mode |
| `SIGNER_MIN_GAS_WEI`, `SIGNER_MIN_DEPOSIT_WEI`, `SIGNER_MIN_RESERVE_WEI` | Positive operator-selected integer readiness thresholds; no universal funding amount is assumed |

`SIGNER_REMOTE_DISCOVERY=true` controls whether the signer exposes its discovery
endpoint; it does not require on-chain discovery. When `SIGNER_ORCH_ADDR` is
nonempty, the pinned upstream signer uses that static list instead of its
on-chain candidate source and periodically refreshes capabilities and pricing
from only those orchestrators. Entries are service endpoints, not Ethereum
addresses. DNS names and IPv4 addresses are supported by this deployment
wrapper; specify an explicit port, omit whitespace, and do not include paths,
credentials, queries, or fragments.

Alternatively, set `SIGNER_REMOTE_DISCOVERY=false` and configure `-orchAddr`
directly on every separately operated gateway. The reference Compose stack does
not include a gateway process, so that gateway-side setting is outside this
repository's `.env` contract.

Mount the individual encrypted keyfile and password read-only. The wrapper
validates exactly that keyfile, copies it alone into a private tmpfs directory,
and passes the isolated directory to go-livepeer. This avoids the pinned
binary's incorrect keyfile-path fallback to `/data/keystore`, where it would
create a different account. Never place unrelated keys in the signer tmpfs.
Keep runtime state separately writable under `/data`. The container starts a
fixed, root-owned entrypoint only long enough to read exact protected mounts
and copy custody inputs into a container-local tmpfs. It then permanently drops its
identity, groups, capability sets, and privilege-gain ability before replacing
PID 1 with go-livepeer as numeric UID/GID `10001`; provision `/data` ownership
before startup. A password that
cannot unlock the encrypted file is rejected by go-livepeer itself after the
static checks. Preflight checks the envelope and address, not cryptographic
password correctness.

Use Compose secrets or read-only bind mounts, not Dockerfile `ARG`, `ENV`,
`COPY`, or image layers for real keys/passwords. Keep populated `.env`, key
files, passwords, and `/data` outside version control. Back up the encrypted
key and data independently; store the password separately from that backup.

## RPC URL logging boundary and accepted risk

The pinned upstream binary unconditionally prints its configuration at
startup. It redacts `EthPassword`, `KafkaPassword`, and webhook headers, but
**does not redact `EthUrl`**. Moving a provider key from argv to an environment
variable does not prevent this logging. At the project owner's explicit
direction, the clearinghouse accepts custom RPC paths and query credentials so
the reference stack can use provider URLs directly. The wrapper emits a generic
warning without repeating the value, but upstream logs may expose the complete
URL to anyone with container-log access.

Restrict and audit access to Docker/container logs, configure short-lived or
least-privilege provider credentials where available, and rotate a credential
after suspected log exposure. A credential-free private relay such as
`http://rpc-relay:8545/rpc` remains the safer production topology. Optional
go-livepeer redaction work is tracked separately and must be reviewed, released,
and adopted through a pinned image upgrade before this risk can be considered
removed. URL userinfo and fragments remain rejected.

## Compose integration contract

The runtime Makefile/Compose work owns stack orchestration. It should build the
signer with the Dockerfile above and apply these service settings:

- Service `remote-signer`, `platform: linux/amd64`, with a root-owned immutable
  entrypoint that replaces PID 1 with the signer as UID/GID `10001` and empty
  effective/bounding capability sets under `NoNewPrivs`.
- Read-only root filesystem; `/data` persistent and writable; a private `/tmp`
  tmpfs if the runtime needs it; drop capabilities and enable no-new-privileges.
- Signing listener `0.0.0.0:8935` is reachable only on the private application
  network. If local clients require host access, publish
  `127.0.0.1:${SIGNER_HOST_PORT:-18935}:8935`. Protect remote access with TLS and
  network policy. Bearer sessions are checked by the mandatory webhook.
- CLI/admin listener is always `127.0.0.1:4935` **inside the container**. Never
  publish or proxy this unauthenticated funding/control API.
- Redpanda's internal listener remains private. Provision the Kafka topic
  before starting the signer; wait for API and broker readiness.
- Always pass `-monitor=true`, Kafka settings, and the compatibility webhook.
  The wrapper explicitly sets `-remoteSignerAllowNoAuth=false` and rejects
  extra flags. Arbitrary `LP_*` passthrough settings are outside this contract.

Container DNS names such as `api` and `redpanda` do not resolve on the host.
Run network checks from a trusted diagnostic container on the application
network. To read admin status, run diagnostics in the signer's network
namespace, for example a separately built Python backend image with Docker
`--network container:<signer-container>`. Supply only required environment
values and read-only secret mounts. Do not make the admin port public merely
to let a health check reach it.

## Readiness before traffic

Static validation from the checkout (requires Python 3.11+):

```sh
python3 deploy/signer/preflight.py --env-file .env
```

Add `--rpc` from a network that can resolve the RPC endpoint. It verifies chain
ID, controller bytecode, gas balance threshold, and that a block number can be
read. This proves RPC access, not block freshness or provider consensus; monitor
chain lag separately. Add `--webhook` from the application network to verify
that the authenticated compatibility endpoint returns a typed denial for an
intentionally invalid user session. It never requests a valid payment.

Inside the signer's network namespace, add
`--admin-url http://127.0.0.1:4935`. This verifies the running signer's address,
chain ID, TicketBroker deposit and remaining reserve against the configured
thresholds, and absence of a scheduled withdrawal. The read-only upstream
endpoints are `/ethAddr`, `/EthChainID`, and `/senderInfo`.

Use the broker's `rpk cluster info` and
`rpk topic describe livepeer-gateway-events` from the internal broker network
to verify metadata access, topic partitions, and replicas. A TCP connection
alone is not broker readiness. Confirm consumer assignment and lag in the
clearinghouse, then exercise the signer-to-authorize-to-Kafka path in the
Compose integration suite before admitting real user traffic. Keep admission
closed if any required check fails. A process listening on its HTTP port is
only live, not financially ready.

The operator funds the wallet with gas and separately deposits/reserves ETH
in TicketBroker using trusted wallet tooling or the private upstream admin
workflow. Funding is a deliberate chain transaction, never part of a health
check. The read-only checks above are repeatable without moving money. Track
wallet gas, deposit, reserve, unlock/withdrawal state, and remaining lease
exposure independently; a wallet balance alone is insufficient.

## Metering and incident behavior

Clearinghouse clients first create a short-lived signer session with
`POST /v1/sessions`. The caller must be an account-scoped
`credential_holder`, authenticate with either its browser session plus CSRF or
an API credential, and supply a unique 16–256 character `Idempotency-Key`.
The response contains the bearer token exactly once and is marked
`Cache-Control: no-store`; only its HMAC digest is stored. A retry cannot
reproduce the secret and returns `409 Conflict`. Recover by listing sessions,
revoking the uncertain session, and creating a replacement with a new key.

The public `signer_url` and `discovery_url` returned to clients must be
browser-reachable URLs. They are distinct from the private Compose service URL
used by the API and remote-signer containers. Production public URLs require
HTTPS and all configured signer URLs reject user information, query strings,
and fragments.

Every session owns one immutable wei lease. Admission snapshots the selected
policy, rate card, funded balance, account/global caps, and current exposure.
Database admission uses a shared exposure serialization point followed by
account, policy/credential, session, lease, and signer-state locks. This same
serialization is used by balance, cap, policy, and rate publication writes.
Expired or revoked sessions release only `available`; `pending` remains held.
Operators can inspect sessions/leases and operate the audited global kill
switch through the documented `/v1` operations endpoints.

Keep the authorization webhook's response `expiry` at zero. Its HTTP 200 body
contains the policy status; returning transport HTTP 402/503 instead changes
upstream client behavior. See [authorization compatibility](../../contracts/http/v1/go-livepeer-authorize.md)
and [two-evidence metering](../design-docs/remote-signer-metering.md).

go-livepeer puts `create_signed_ticket` and unrelated monitoring
`GatewayEvent` values on the same configured topic. The clearinghouse ignores
unrelated types after validating the bounded generic envelope, and validates
the complete pinned shape only for `create_signed_ticket`. One such event is
enqueued per signed request/batch, before response construction/delivery; it is
not proof the caller received the response. The producer can drop events on
queue saturation or exhausted retries. Alert on producer queue drops, producer
error logs, broker failures, consumer lag, missing sequences, and aging
reservation receipts. Keep pending value reserved when confirmation is
missing. See the metering document for reconciliation and the
unresolved-final-event limitation.

Operational inspection is read-only through `GET /v1/usage`,
`GET /v1/charges`, `GET /v1/operations/reconciliation`, and
`GET /v1/operations/metering-health`. These routes accept bounded opaque
account/cursor values and enforce tenant scope. They do not repair, retry,
release, or acknowledge financial state. Quarantine stays durable in
PostgreSQL; no Kafka dead-letter publisher is part of this slice.

If secrets are missing, address/chain differs, the key cannot unlock, or the
broker/API is unavailable, fix the named configuration or dependency before
enabling traffic. Do not disable the webhook or monitoring to clear a readiness
failure. Rotate leaked webhook credentials on both API and signer; rotate a
compromised signing key through an operator-controlled funding/custody process.

## Evidence and validation

Wire behavior is grounded in the pinned upstream
[remote signer](https://github.com/livepeer/go-livepeer/blob/e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6/server/remote_signer.go),
[startup configuration](https://github.com/livepeer/go-livepeer/blob/e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6/cmd/livepeer/starter/starter.go),
[native flags](https://github.com/livepeer/go-livepeer/blob/e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6/cmd/livepeer/starter/flags.go),
and [Kafka producer](https://github.com/livepeer/go-livepeer/blob/e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6/monitor/kafka.go).

Run the isolated tests with
`python3 -m unittest discover -s tests/deploy -v`. They cover shell refusal
paths, secret-safe diagnostics, encrypted-key/address checks, literal env
parsing, network mismatch, underfunding, pending withdrawals, and webhook
denial semantics. Live chain/funding checks require operator credentials and
are reported separately from deterministic test results.

The disposable [recovery qualification](recovery-qualification.md) exercises
the pinned image, wrapper validation restart, fail-closed protocol routes,
broker/database/consumer restarts, poison quarantine, and a real Redpanda
retention gap. It deliberately leaves funded transaction signing to the
operator-owned gate described above.
