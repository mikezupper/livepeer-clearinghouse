# Supply-chain and release security

The canonical publisher is `livepeer/clearinghouse`. Forks can run every
read-only security check, but the release guard refuses to publish from another
repository. Pull-request workflows do not consume repository secrets.

## Continuous checks

`security.yml` runs dependency review, CodeQL for Python and
JavaScript/TypeScript, OSV scans of both lockfiles, `npm audit`, and Trivy
dependency, secret, license, container-file, and infrastructure checks. The
separate Scorecard workflow runs only on trusted branch, scheduled, manual, and
branch-protection events. Every action reference is an immutable full commit
SHA; the readable version comment is only an update hint.

Dependabot checks Python, npm, GitHub Actions, and every Dockerfile directory.
An action or scanner update is reviewed like application code: verify the
upstream release and commit, update the full SHA and version comment together,
then run `python3 scripts/validate_supply_chain.py` and actionlint.

## Release contract

A release starts from an existing `vMAJOR.MINOR.PATCH` SemVer tag (SemVer
pre-release suffixes are also accepted; build metadata is rejected because it
is not a portable OCI tag). The tag version must exactly
match `project.version` in `pyproject.toml`, resolve to the checked-out commit,
and belong to the canonical repository. Manual recovery is allowed only when
the workflow itself is dispatched from `main` and still checks out and validates
the named immutable tag.

The protected `release` environment should require an independent reviewer.
Repository Actions settings must allow GitHub Actions to create packages and
attestations. No long-lived registry or signing credential is configured:
publication uses the scoped `GITHUB_TOKEN`, and Cosign uses GitHub's short-lived
OIDC identity.

The workflow builds each Linux amd64 image under a commit-addressed staging
reference. A recovery run may repush that reference, so the captured digest—not
the staging tag—is the immutable identity:

- `ghcr.io/livepeer/clearinghouse-backend`
- `ghcr.io/livepeer/clearinghouse-admin-web`
- `ghcr.io/livepeer/clearinghouse-user-web`
- `ghcr.io/livepeer/clearinghouse-edge`
- `ghcr.io/livepeer/clearinghouse-ops`
- `ghcr.io/livepeer/clearinghouse-remote-signer`

Trivy qualifies the exact pushed digest. Syft produces SPDX JSON and CycloneDX
JSON from that same digest. GitHub stores build-provenance and SBOM attestations,
and Cosign signs and immediately verifies the digest using the workflow's exact
OIDC identity. Only after every image passes does the publish job attach SemVer
(and, for stable versions, `latest`) tags to those existing digests; it never
rebuilds them.

The GitHub release contains image descriptors, both SBOM formats, per-image
checksums, a deterministic public-contract archive and manifest, a global
`SHA256SUMS`, and the Sigstore provenance bundle. Existing GitHub releases are
treated as immutable and make a rerun fail closed. Before promotion starts, the
workflow also checks every canonical `image:VERSION` tag and refuses the entire
promotion when any already exists; partial releases require explicit operator
reconciliation rather than overwriting a SemVer image tag.

## Consumer verification

Download release files and verify their bytes before use:

```bash
sha256sum --check SHA256SUMS
gh attestation verify --repo livepeer/clearinghouse \
  --bundle release-assets.provenance.sigstore.json \
  clearinghouse-1.2.3-contracts.tar.gz
```

Resolve an image tag to its digest, then verify the digest rather than trusting
the mutable tag:

```bash
IMAGE=ghcr.io/livepeer/clearinghouse-backend
DIGEST=sha256:replace-with-release-descriptor-digest
cosign verify \
  --certificate-identity-regexp \
  '^https://github.com/livepeer/clearinghouse/.github/workflows/release\.yml@refs/(tags/v1\.2\.3|heads/main)$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "${IMAGE}@${DIGEST}"
gh attestation verify "oci://${IMAGE}@${DIGEST}" --repo livepeer/clearinghouse
```

`heads/main` is accepted only for a manual recovery run. Normal tag-triggered
releases have the exact `refs/tags/v1.2.3` identity. Compare the digest with the
corresponding `*.image.json` release descriptor before deploying it.

The complete maintainer procedure, compatibility/version policy, repository
settings, dependency policy, and rollback boundary are documented in
[Releases, versions, and compatibility](../RELEASING.md).
