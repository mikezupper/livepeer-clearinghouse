# Deployment

## Reference profile

The default Compose project runs edge, core, Redpanda, and remote-signer. It publishes only loopback port 8080 and persists `sqlite-data`, `redpanda-data`, and `signer-data` volumes.

1. Run `make init-env` and restrict `.env` to its owner.
2. Configure Resend, admin email, three independent peppers/secrets, public URL/origin, and signer inputs.
3. Store the encrypted V3 keystore and password outside the checkout; restrict their modes.
4. Run `make signer-preflight`.
5. Run `make build`, `make up`, then `make smoke` and `make ps`.
6. Place a TLS reverse proxy/load balancer in front of the loopback edge for production and set the public HTTPS URL, exact origin, and secure-cookie flag.

`make down` preserves data. `make destroy DESTROY=1` irreversibly removes the three Compose volumes. Take and verify backups before upgrades or destruction.

## SQLite lifecycle

The database lives at `/data/clearinghouse.db` inside `sqlite-data`. Schema creation is idempotent at core startup. For backup, use a filesystem snapshot only while core is stopped, or use SQLite's online backup mechanism from trusted deployment tooling. A restore replaces the complete database while core is stopped and is accepted only after `PRAGMA integrity_check` and an application readiness check.

Single-node SQLite is not horizontally writable. An enterprise PostgreSQL deployment supplies a separately packaged `CoreStore` adapter, explicitly wires it into its composition root, and passes the storage conformance suite before cutover.

## Updates and rollback

Build immutable images through the Makefile. Before deployment, run `make test` and preserve a verified database backup. Roll forward by replacing core/edge images; SQLite schema setup remains additive and idempotent. Roll back only to a version proven compatible with the stored schema and contracts.
