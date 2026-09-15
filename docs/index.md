# Project knowledge index

This directory is the system of record for durable product and engineering
knowledge. `AGENTS.md` is its map; Beads is the only source of work status.

## Product

- [Walking-slice specification](product-specs/walking-slice.md)
- [Product specifications index](product-specs/index.md)

## Design

- [Design documents index](design-docs/index.md)
- [Simplified core contracts](design-docs/simple-core.md)
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
- [Reliability](RELIABILITY.md)
- [Work tracking](WORK_TRACKING.md)

## Public compatibility contracts

- [HTTP API](../contracts/openapi.yaml)
- [Signer-event input](../contracts/asyncapi.yaml)
- [go-livepeer authorization callback](../contracts/http/v1/go-livepeer-authorize.md)
- [Python gateway workload SDK token](../contracts/sdk/livepeer-python-gateway-v1.md)
- [SQLite and storage-adapter invariants](../contracts/domain/v1/database.md)
- [Build-time extension ports](../contracts/ports/v2/protocols.py)
- [Core event envelope](../contracts/events/v2/core-event.schema.json)

## Operations

- [Operations handbook and runbook index](operations/index.md)
- [Deployment and Compose runtime](operations/deployment.md)
- [Remote-signer operations](operations/signer.md)

## References

References explain motivation and provenance but do not override implemented
contracts or the documents above.

- `references/OpenClearinghouseBlueprint.pdf`
- `references/openai-harness-engineer.md`
