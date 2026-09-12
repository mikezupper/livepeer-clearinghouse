# Supply-chain controls

Runtime bases and the unmodified go-livepeer signer are pinned by immutable image digest. Python and frontend dependencies are locked. GitHub Actions are pinned to commit SHAs, use least-privilege permissions, and never use privileged pull-request execution.

The release publishes and scans exactly:

- `ghcr.io/livepeer/clearinghouse-core`
- `ghcr.io/livepeer/clearinghouse-edge`
- `ghcr.io/livepeer/clearinghouse-remote-signer`

Each released digest receives SPDX/CycloneDX metadata and provenance/signature attestations before an immutable version tag is promoted. Operators should verify the digest and attestations, mirror images into their controlled registry, and retain the lockfiles and release checksums with deployment evidence.
