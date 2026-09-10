# Changelog

This file records user-visible changes to Open Clearinghouse. It follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the distribution
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Contract
compatibility has its own versioning rules; see the
[release and compatibility policy](docs/RELEASING.md).

## [Unreleased]

### Added

- Initial production walking slice for walletless Livepeer spend, including the
  Python API and metering consumer, PostgreSQL, Redpanda, an unmodified pinned
  go-livepeer remote signer, and separate Lit admin and user applications.
- Email one-time-code authentication through a configurable Resend-compatible
  endpoint, with optional Google and GitHub identity providers.
- Auditable authorization, lease, usage, charge, ledger, reconciliation,
  operational, and release-qualification paths.

[Unreleased]: https://github.com/livepeer/clearinghouse/commits/main
