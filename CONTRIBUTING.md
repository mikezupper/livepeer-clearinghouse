# Contributing to Open Clearinghouse

Thank you for helping build a vendor-neutral clearinghouse for walletless
Livepeer network spend. Contributions are welcome as bug reports, design
proposals, documentation, tests, and code.

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Report
vulnerabilities through the private process in [SECURITY.md](SECURITY.md), not
through a public issue.

## Before proposing a change

Read the [architecture](ARCHITECTURE.md), the
[walking-slice specification](docs/product-specs/walking-slice.md), and the
[documentation index](docs/index.md). Important implementation constraints are
also summarized in [AGENTS.md](AGENTS.md).

GitHub Issues are public intake for reproducible bugs and scoped proposals.
They are not the implementation tracker. Maintainers record accepted project
work and its dependencies in Beads. If a change already has a Beads ID, include
it in the pull request; otherwise a maintainer will create or link one during
triage.

For architecture or product changes, open a feature proposal before investing
in an implementation. Small, self-contained fixes may go directly to a pull
request.

## Development environment

The supported toolchain is Python 3.14, `uv` 0.12.12 or newer, Node.js 24,
`npm`, Docker with Compose v2, GNU Make, and the `bd` CLI. Install dependencies
from the committed lockfiles:

```sh
uv sync --frozen --all-groups
cd frontend
npm ci --ignore-scripts
cd ..
make prepare-development-secrets
make validate
```

For an interactive local deployment, create a private environment file once,
replace every placeholder, and follow the deployment runbook:

```sh
make init-env
make deployment-preflight
```

`make up` requires a funded, explicitly configured remote signer. The keyless
development stack is `make up-core`. See
[deployment](docs/operations/deployment.md) and
[signer operations](docs/operations/signer.md) before either command.

## Working with Beads

Repository maintainers and agents use Beads as the only work tracker:

```sh
bd prime
bd ready --json
bd show <id> --json
bd update <id> --claim
```

Record discovered work with a `discovered-from` dependency instead of adding a
TODO file, plan document, or GitHub checklist. Close the bead with a reason only
after its acceptance criteria and required checks pass. See
[work tracking](docs/WORK_TRACKING.md) for the complete workflow.

## Engineering expectations

- Parse untrusted values at HTTP, broker, environment, and database boundaries.
- Keep domain code independent of infrastructure and make adapters explicit.
- Use integers or exact decimals for quantities and money; never floating point.
- Preserve tenant isolation, idempotency, auditability, and fail-closed behavior.
- Never commit credentials, signer keys, OTPs, local `.env` files, or production
  data.
- Keep Pymthouse integrations optional; they are not core dependencies.
- Use Lit and Effect for TypeScript application work. Keep markup semantic, put
  application-wide presentation in `global.css`, and test behavior in browsers.
- Update contracts, migrations, documentation, and generated artifacts whenever
  behavior changes.

## Tests and quality gates

Run the narrowest relevant Make target while iterating. Before requesting
review, run the full required suite:

```sh
cd frontend
npx playwright install chromium firefox webkit
cd ..
make test
```

The backend, each web application, and each shared frontend package must
independently meet at least 85% for lines, statements, functions, and branches.
Do not weaken thresholds, skip failures, or update generated contracts without
reviewing the semantic diff.

Docker-backed tests use disposable projects but may consume meaningful local
resources. The full suite requires a working Docker daemon and supported
browsers.

## Pull requests

Create a focused branch from `main` and keep unrelated changes separate. A
reviewable pull request explains motivation and behavior, identifies risk and
migration impact, names its Beads work when available, and lists the exact
validation performed. Add or update tests for observable behavior and document
operator-facing changes.

Maintainers may request a rebase, split, additional evidence, or design review.
Approval does not bypass required status checks. The project uses the merge
method configured on the canonical repository; contributors should not rewrite
published history merely to change merge style.

By contributing, you agree that your contribution is licensed under the
repository's [MIT License](LICENSE).
