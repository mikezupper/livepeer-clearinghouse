# Changelog

This file records user-visible changes to Open Clearinghouse. It follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the distribution
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Contract
compatibility has its own versioning rules; see the
[release and compatibility policy](docs/RELEASING.md).

## [Unreleased]

### Added

- Initial production-safe, single-node walking slice for walletless Livepeer
  workloads: one supervised Python API and Kafka consumer with durable SQLite,
  Redpanda, an unmodified pinned go-livepeer remote signer, and separate Lit
  admin and user applications served by one edge.
- Email one-time-code authentication through a configurable Resend-compatible
  endpoint, with optional Google and GitHub identity providers.
- Exact-price capability discovery, immutable workload quotes, short-lived SDK
  access, fail-closed signer authorization, idempotent usage attribution, and
  separate quote-derived and signer-reported cost totals.
- Explicit build-time identity, discovery, authorization-policy, event-sink,
  and storage boundaries for deployment-owned extensions.

### Changed

- Reduced the reference distribution to the small identity, discovery,
  authorization, and metering core. Enterprise tenancy, balances, billing,
  ledger, compliance, and support workflows are external versioned layers, not
  dormant services or database tables in the core.
- Made SQLite the bundled authoritative store. A PostgreSQL implementation is
  an optional, separately packaged adapter that must pass the core storage
  conformance suite; it is not a reference runtime dependency.

[Unreleased]: https://github.com/livepeer/clearinghouse/commits/main
