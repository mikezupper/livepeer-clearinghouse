# Reference Compose deployment

The reference distribution uses `compose.yaml` and the root `Makefile`. It is
one PostgreSQL database, one single-node Redpanda broker, one API process, one
metering worker, the unmodified pinned go-livepeer remote signer, separate
admin and user static applications, and a same-origin Caddy edge. No OpenMeter,
Stripe, Auth0, Turnkey, or Pymthouse service is present.

## Commands

Run `make help` for the executable command index. `make init-env` copies the
safe example to a mode-0600 `.env`; review every placeholder before using the
real signer. `make build` builds all project images. `make up` validates the
signer prerequisites and starts the default stack. `make down` preserves data;
volume deletion requires the explicit `make destroy DESTROY=1` guard.

`make test` runs repository checks, provisions an isolated PostgreSQL/Redpanda
test project, executes the ordered backend unit and real-service suites, enforces
all four coverage metrics, runs each frontend gate independently, and removes
the test project and volumes on success or failure. `make smoke` starts the named
core services without the signer and submits a canonical synthetic signer event.
It proves edge headers and routing, database readiness, authenticated webhook
denial, broker delivery, consumer heartbeat, normalization, quarantine, and
durable offset processing. It moves no money and sends no email.

`make prepare-development-secrets` generates ignored mode-0600 values under
`tmp/runtime-secrets`; it never creates a signer key or password. The local
core and preflight targets invoke it automatically. Production sets each
`*_HOST_FILE` variable to an operator-managed file outside the checkout.
When the deployment operator is not UID 1000, set
`CLEARINGHOUSE_SECRET_OWNER_UID` to the output of `id -u`; startup rejects
source files owned by any other non-root account.
Backend containers start through a fixed root entrypoint solely to read those
protected, read-only source mounts. The entrypoint accepts only canonical
`/run/secrets/*` paths, rejects symlinks and unsafe modes, bounds each read,
removes file indirection, and permanently drops to UID/GID 10001 with an empty
capability bounding set before starting Python. No Docker volume retains the
resolved values. The signer similarly copies its protected custody inputs into
a container-local mode-0400 tmpfs, then drops to UID/GID 10001; stopping the
container destroys those copies.
PostgreSQL reads `POSTGRES_PASSWORD_FILE`; the backend entrypoint percent-encodes
that same protected value while constructing its private DSN, so neither the
password nor a password-bearing URL appears in the rendered Compose model.

## Why the deterministic smoke excludes the signer

The signer is a default Compose service, not an optional profile. The accepted
wrapper and unmodified upstream binary deliberately have no keyless test mode.
Real startup requires an operator-owned encrypted V3 keystore and separate
password, a credential-free RPC URL, the selected chain/controller, a matching
address, gas, and TicketBroker deposit/reserve. Inventing a local plaintext key,
silently funding it, bypassing the webhook, or replacing the signer would break
the custody and compatibility contract.

Consequently `make up` first runs `make signer-preflight`; it fails with a
named, secret-safe configuration error until those prerequisites exist.
`make smoke` is the deterministic CI/developer path and explicitly starts the
core service list. `make signer-smoke` is operator-gated and runs a diagnostic
container in the signer's network namespace. It checks the loopback-only admin
listener's address, chain, deposit, reserve, and withdrawal state, plus RPC
access, authenticated webhook denial, broker metadata, and topic presence. An
actual funded signing request is a
separate deliberate operator action described in [signer operations](signer.md).

## Ordering and bootstrap

PostgreSQL and Redpanda must become healthy. `redpanda-init` idempotently
creates the signer topic with exactly one partition, replication factor one,
delete-only cleanup, and at least seven-day retention. An existing topic with
different partitions, replication, cleanup, or retention is refused instead of
silently changing the metering trust boundary. `migrate` then applies the locked Alembic history.
`bootstrap-operator` runs once after migration and exits successfully when its
paired variables are absent. Only that job receives the bootstrap email and
secret. Re-running identical bootstrap configuration is safe; conflicting
configuration fails closed. API startup follows successful bootstrap, while
the consumer follows migration and topic creation.

The local reference broker uses replication factor one and development mode.
That is reproducible, not highly available. Production must use an
independently operated multi-node broker with authentication, authorization,
replication, monitoring, and tested recovery while preserving the configured
topic's one-signer trust binding.

## Network and process boundaries

Only `127.0.0.1:${CLEARINGHOUSE_EDGE_PORT:-8080}` is published. Caddy routes
`/v1` and `/health` to the API, `/admin/` to the admin app, and the three exact
go-livepeer protocol paths (`/generate-live-payment`, `/sign-orchestrator-info`,
and `/discover-orchestrators`) to the signing listener. `/` serves the user app.
These root routes are required because go-livepeer resolves absolute paths and
would discard a `/signer` base prefix. The unauthenticated signer admin
listener on container loopback port 4935 is neither published nor proxied.
PostgreSQL and Redpanda have no host ports.

The API accepts proxy headers because it is not published and its only public
ingress is the same-origin Caddy edge on the private application network. This
trust boundary is required for client-address abuse controls. A deployment that
publishes the API directly, attaches an untrusted workload to that network, or
adds another ingress must replace the wildcard Uvicorn forwarded-IP setting
with the exact proxy addresses before serving traffic.

The database and broker networks are separate and internal. Redpanda is not on
the edge/web application network. API and signer have a separate egress network
for Resend/OAuth and chain RPC respectively. Application containers run without
Linux capabilities, with no-new-privileges and read-only roots; only named data
volumes and explicit tmpfs mounts are writable. The signer is pinned to
linux/amd64 because its upstream index has no arm64 runtime image. Its Compose
health check proves only that the private signing listener is accepting TCP.
The consumer health check requires a fresh durable worker heartbeat; because
the supervised worker exits when its Kafka task fails, this also gates core
startup on the live worker/broker loop. The operator diagnostic remains the
read-only gate for signer address, chain, gas, deposit, reserve, webhook, and
broker topic readiness. Neither check proves an actual signing transaction.

The edge sets a restrictive CSP, clickjacking, MIME-sniffing, referrer, and
permissions headers. Local HTTP requires `CLEARINGHOUSE_AUTH_COOKIE_SECURE=false`.
Production must terminate trusted HTTPS, set secure cookies, configure exact
public OAuth/origin/signer URLs, and adapt the edge policy to its domain.

## Secrets and images

`.env`, generated development secrets, keystores, password files, and common
private-key formats are ignored and excluded from the Docker context. Backend
entrypoint support for an allowlist of fixed-path `*_FILE` variables permits Compose
secret mounts without accepting arbitrary environment indirection. Setting
both a direct value and its file form fails startup. API receives its required
auth secrets; bootstrap receives only its auth pepper and optional paired
bootstrap values; the metering worker receives no auth, mail, OAuth, credential,
or signer secrets. The real signer receives its key and password as individual
read-only bind mounts whose host paths must be outside the checkout, plus its
webhook token as a Compose secret. None is copied into an image.

Production preflight rejects the example PostgreSQL password, passwords equal
to the database user, weak passwords, and every required host secret path inside
the checkout. The `clearinghouse` values in `.env.example` are local-only and
cannot pass a production start.

Every external image uses an immutable registry digest: Python 3.14.7 slim,
uv 0.12.12, Node 26.8.1 slim, PostgreSQL 18.6, Redpanda 26.2.2, Caddy 2.11.4,
and the audited go-livepeer commit recorded in signer operations. Updating a
digest requires dependency review plus rebuild, deployment tests, clean smoke,
and signer contract qualification where applicable.
