# Reliability

The bundled distribution is a production-safe single-node profile, not a high-availability claim.

- SQLite uses WAL, foreign keys, a busy timeout, transactional schema initialization, and one supervised writer process.
- Redpanda and the remote signer have persistent named volumes and health checks.
- Core readiness requires both SQLite and its Kafka consumer when metering is enabled.
- The consumer retries broker failures, commits only after decoding/ingestion, and uses idempotent usage IDs.
- Authorization fails closed for unknown, expired, revoked, repriced, mismatched, rebound, or globally stopped work.
- Discovery observations expire and must be copied into immutable workload quotes. A partial signer refresh replaces only sources it actually reports, retaining missing sources' last valid observations until their bounded TTL.

Back up the stopped SQLite volume or use SQLite's online backup API through deployment tooling. Test restore procedures before relying on them. Operate replicated Kafka and a separately qualified PostgreSQL adapter when one-node durability or throughput is insufficient.

Alert on core readiness, broker availability, signer errors, unmatched usage, and divergence between quote-derived and signer-reported cost. A final Kafka event can still be lost by an upstream asynchronous producer; the core reports only evidence it has actually received.
