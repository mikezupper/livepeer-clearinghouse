# Project knowledge index

This directory is the system of record for durable product and engineering
knowledge. `AGENTS.md` is its map; Beads is the only source of work status.

## Product

- [Walking-slice specification](product-specs/walking-slice.md)
- [Product specifications index](product-specs/index.md)

## Design

- [Design documents index](design-docs/index.md)
- [Core beliefs and invariants](design-docs/core-beliefs.md)
- [Adapter loading and deployment](design-docs/adapter-loading.md)
- [Remote-signer metering](design-docs/remote-signer-metering.md)
- [Authentication](design-docs/authentication.md)
- [Top-level architecture](../ARCHITECTURE.md)

## Engineering policy

- [Frontend](FRONTEND.md)
- [Quality](QUALITY.md)
- [Releases, versions, and compatibility](RELEASING.md)
- [Changelog](../CHANGELOG.md)
- [Security](SECURITY.md)
- [Threat model](security/threat-model.md)
- [Data classification and lifecycle](security/data-lifecycle.md)
- [Reliability](RELIABILITY.md)
- [Work tracking](WORK_TRACKING.md)

## Operations

- [Operations handbook and runbook index](operations/index.md)
- [Deployment and Compose runtime](operations/deployment.md)
- [Observability and service levels](operations/observability.md)
- [Database backup and recovery](operations/database-recovery.md)
- [Broker and consumer recovery](operations/broker-recovery.md)
- [Metering integrity](operations/metering-integrity.md)
- [Secret and key rotation](operations/secret-rotation.md)
- [Migration and release rollback](operations/migration-rollback.md)
- [Incident response](operations/incident-response.md)
- [Retention and legal holds](operations/retention.md)
- [Kill-switch reopening](operations/kill-switch.md)
- [Capacity qualification](operations/capacity.md)
- [Remote-signer operations](operations/signer.md)

## References

References explain motivation and provenance but do not override implemented
contracts or the documents above.

- `references/OpenClearinghouseBlueprint.pdf`
- `references/openai-harness-engineer.md`
