# Releasing

Releases use immutable SemVer tags and publish three OCI images:

- `ghcr.io/livepeer/clearinghouse-core`
- `ghcr.io/livepeer/clearinghouse-edge`
- `ghcr.io/livepeer/clearinghouse-remote-signer`

Before tagging, update `CHANGELOG.md`, make the project version match the tag, run `make test`, run `make build`, and qualify the signer in the intended environment with `make signer-preflight`. Tags and published image versions are immutable. The release workflow scans images, emits SBOMs, signs/attests digests, and promotes the already-qualified digest rather than rebuilding it.

Contract compatibility is explicit: additive optional response fields are minor-version changes; changed meanings, removed fields/routes, identifiers, price arithmetic, or signer attribution require a major version. SQLite schema evolution must remain forward compatible or include a documented backup/restore migration.
