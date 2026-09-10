# Releases, versions, and compatibility

Open Clearinghouse publishes one versioned distribution from the canonical
`livepeer/clearinghouse` repository. A distribution release is a coordinated
set of six OCI images plus a snapshot of its public contracts. The repository
does not publish its private frontend workspaces as npm packages.

## Version model

The distribution follows Semantic Versioning. `project.version` in
`pyproject.toml` is the source version and a release tag must be exactly
`vMAJOR.MINOR.PATCH`, optionally with a SemVer pre-release suffix. Build
metadata is not accepted because it is not portable as an OCI tag. The same
version is recorded in `uv.lock`, every image descriptor and the deterministic
contract-bundle manifest. The private frontend workspace versions remain
`0.0.0` and are not release coordinates.

- Increment **major** for an incompatible deployment, configuration,
  persistence, operator, or public-contract change.
- Increment **minor** for backward-compatible behavior or public-contract
  additions.
- Increment **patch** for backward-compatible corrections and security fixes.
- Use a pre-release such as `1.0.0-rc.1` for qualification that must not update
  the `latest` image pointers.

Before `1.0.0`, minor releases may contain compatibility breaks under SemVer.
They must still be called out under `Changed` or `Removed` in the changelog,
carry migration and rollback guidance, and obey the contract-major rules below.

Distribution and contract versions are related but not interchangeable:

| Coordinate | Current source | Meaning | When it changes |
| --- | --- | --- | --- |
| Distribution | `pyproject.toml` `project.version` (`0.1.0`) | Deployable image set and bundled configuration/contracts | Every release |
| HTTP description | `contracts/openapi.yaml` `info.version` (`0.1.0`) | Revision of the OpenAPI document | Patch/minor/major according to changes in that document |
| HTTP compatibility major | `/v1` paths and `contracts/http/v1` | Client compatibility boundary | A breaking HTTP change requires a new major namespace; keep the old namespace through its documented support window |
| Event description | `contracts/asyncapi.yaml` `info.version` (`1.0.0`) | Revision of the AsyncAPI document | Patch/minor/major according to changes in that document |
| Event compatibility major | schema `$id` paths under `contracts/events/v1` and payload `schema_version` | Producer/consumer compatibility boundary | A breaking payload or transport-semantics change requires a new major schema identity/version |
| Adapter manifest | `contracts/adapters/v1` and manifest `contract_version` | Adapter loading and capability boundary | A breaking manifest/port change requires a new major contract |

Adding an optional field or endpoint is normally backward compatible; changing
meaning, narrowing accepted input, making an optional field required, removing
a field or operation, or changing identity/idempotency semantics is breaking.
Canonical OpenAPI, AsyncAPI, JSON Schema, Python ports, and TypeScript decoders
must agree. `make quality-contracts` rejects runtime/generated drift. A distribution
may include no contract changes, so contract versions do not automatically
track its version. Release notes must list the exact contract versions and any
supported upgrade combinations.

The frontend currently pins TypeScript `6.0.3`. The installed
`typescript-eslint` toolchain declares support for TypeScript
`>=4.8.4 <6.1.0`, so TypeScript must remain below `6.1.0` until that peer range
is widened and the complete frontend quality/browser suite passes. Node is
constrained by `frontend/package.json` and CI; the lockfile, not a floating
range, selects all frontend tools.

## Prepare a release

1. Start from a pull request targeting `main`. Choose the SemVer impact from
   user-visible behavior, contracts, configuration, database migrations, and
   operator procedures—not commit labels alone.
2. Move applicable entries from `Unreleased` in `CHANGELOG.md` into
   `## [MAJOR.MINOR.PATCH] - YYYY-MM-DD`, add comparison links, and describe
   migrations, compatibility constraints, deprecations, and security impact.
   Do not publish an empty or aspirational changelog entry.
3. Set `project.version` in `pyproject.toml`, run `uv lock`, and verify the root
   package version in `uv.lock`. Do not bump private frontend workspaces.
4. When contracts changed, update their independent versions and compatibility
   namespaces as required, regenerate artifacts, and run
   `make quality-contracts`.
5. Run `make test`. For a production deployment, also run
   `make qualification-evidence` in the intended release topology and retain
   its sanitized JSON and Markdown evidence with the change record. This local
   qualification is not executed by `release.yml`; the workflow independently
   reruns `make test` on the tagged commit.
6. Merge only after the required checks and reviews pass. From an up-to-date,
   clean checkout of that exact `main` commit, create and push an annotated,
   signed tag: `git tag -s vMAJOR.MINOR.PATCH -m "Open Clearinghouse
   vMAJOR.MINOR.PATCH"`, then `git push origin vMAJOR.MINOR.PATCH`.

The tag starts `.github/workflows/release.yml`. Its release guard fails unless
the repository is exactly `livepeer/clearinghouse`, the tag is valid SemVer,
the tag resolves to the checked-out commit, and it matches `project.version`.
A manual workflow dispatch is recovery for an existing tag only; it must be
started from `main` and passes the same guard. It does not mint a version or tag.

## What the workflow publishes

The workflow qualifies the tagged source with `make test`, then builds Linux
amd64 images for these components:

- `ghcr.io/livepeer/clearinghouse-backend`
- `ghcr.io/livepeer/clearinghouse-admin-web`
- `ghcr.io/livepeer/clearinghouse-user-web`
- `ghcr.io/livepeer/clearinghouse-edge`
- `ghcr.io/livepeer/clearinghouse-ops`
- `ghcr.io/livepeer/clearinghouse-remote-signer`

Each build first receives a commit-addressed `sha-<40-character-revision>`
staging reference. That reference may be repushed during recovery and is not an
immutable deployment identity. The workflow captures its `sha256:` digest,
scans that exact digest, generates SPDX JSON and CycloneDX JSON SBOMs from it,
adds GitHub build-provenance and SPDX attestations, signs it keylessly with
Cosign, and writes a checksummed `*.image.json` descriptor.

Only after all builds succeed does the publish job promote those captured
digests—without rebuilding—to write-once `MAJOR.MINOR.PATCH` tags. Stable
releases also update the mutable `latest` pointers; pre-releases do not. Deploy
with `image@sha256:...` from the descriptor. Never use `latest`, a staging
reference, or a bare version tag as the durable deployment record.

The GitHub release contains both SBOM formats and a descriptor/checksum for
each image, a deterministic contracts archive and manifest, global
`SHA256SUMS`, and a Sigstore provenance bundle for the release assets. GitHub
release records and SemVer image tags are intentionally write-once. If a run
partially promotes image tags, stop: do not overwrite or deploy the partial
set. Record the incident, reconcile what was published, correct the cause, and
issue a new patch version.

## Verify before deployment

Download all assets from the GitHub release into an otherwise empty directory.
Verify their bytes and the contract archive's provenance:

```sh
sha256sum --check SHA256SUMS
gh attestation verify --repo livepeer/clearinghouse \
  --bundle release-assets.provenance.sigstore.json \
  clearinghouse-1.2.3-contracts.tar.gz
```

For every component, read `image` and `digest` from its descriptor and verify
the digest rather than a tag:

```sh
IMAGE=ghcr.io/livepeer/clearinghouse-backend
DIGEST=sha256:replace-with-backend.image.json-digest
cosign verify \
  --certificate-identity \
  https://github.com/livepeer/clearinghouse/.github/workflows/release.yml@refs/tags/v1.2.3 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "${IMAGE}@${DIGEST}"
gh attestation verify "oci://${IMAGE}@${DIGEST}" \
  --repo livepeer/clearinghouse
```

For an explicitly recorded manual recovery run, the Cosign certificate identity
ends in `@refs/heads/main`; normal releases must use the exact tag identity.
Inspect the downloaded SPDX or CycloneDX SBOM before approval. Record the six
verified digests, distribution/contract versions, migration revision, evidence
artifact, approver, and deployment time in the external change record.

## Repository controls required before release

These controls live in GitHub settings and cannot be enforced by this checkout:

- Protect `main` with a ruleset that requires pull requests, at least one
  independent approval, resolved review conversations, linear history, and the
  `Required quality aggregate` status check. Block force pushes and deletion;
  restrict bypass to an audited emergency role.
- Protect `v*` tags against update and deletion, and restrict creation to the
  release-maintainer role. Require signed tags in the maintainer procedure.
- Create a `release` environment. Require an independent deployment reviewer,
  prevent self-review, and restrict deployment refs to protected `v*` tags plus
  `main` for manual recovery. Both image-build and publish jobs use it.
- Permit GitHub Actions to create packages and attestations, while retaining
  the workflow's checked-in least-privilege permissions. Do not add registry or
  signing secrets: GHCR uses the scoped `GITHUB_TOKEN`, and Cosign/GitHub
  attestations use short-lived GitHub OIDC credentials.
- Apply GHCR write access only to the canonical repository workflow and keep
  package visibility/access consistent across all six images.

Forks can run read-only checks, but the release guard deliberately refuses to
publish from them.

## Dependency currency

Runtime and development resolutions are locked, actions use full commit SHAs,
and external base images use registry digests. Dependabot proposes Python, npm,
Docker, and GitHub Actions updates weekly. A dependency update must:

1. verify the upstream release and provenance; for an action, update its full
   commit SHA and readable version comment together;
2. regenerate `uv.lock` or `frontend/package-lock.json` with the repository's
   pinned toolchain and include the lockfile diff;
3. confirm peer and runtime constraints, including the TypeScript ceiling
   above, rather than forcing an incompatible install;
4. run the affected focused tests plus `make test`; and
5. run `python3 scripts/validate_supply_chain.py` and review security/scanner
   output before merge.

Major upgrades are separate reviewed changes with migration and rollback notes.
“Latest” means the latest version that satisfies these compatibility and
qualification gates, not an unreviewed floating range.

## Rollback and recovery boundaries

A release cannot be unpublished or rewritten to perform rollback. Redeploy a
previously verified image set by its six recorded digests and follow the
[migration and release rollback runbook](operations/migration-rollback.md).
The prior application must remain compatible with the live database and
contract versions. If new writes make downgrade lossy or a migration guard
refuses, keep admission closed and forward-fix or restore the verified encrypted
backup; never use Alembic `stamp` to manufacture compatibility.

Rollback also does not rewind Kafka history, checkpoints, ledger facts,
external emails, signer actions, or third-party identity state. Reconcile usage,
charges, balances, reservations, and metering checkpoints before reopening the
kill switch. Preserve the failed and restored digests plus incident evidence.
The mutable `latest` pointer is never rollback evidence.
