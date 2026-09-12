# Open Clearinghouse agent map

Open Clearinghouse is a vendor-neutral engine for walletless Livepeer network
spend. The reference distribution is one Python/SQLite service, Redpanda,
go-livepeer remote-signer, and separate Lit admin and user applications.

## Start every session

1. Run `bd prime`, then `bd ready` and `bd list --status in_progress`.
2. Claim a ready bead before changing code. Beads is the only work tracker.
3. Read the documents linked below for the area being changed.
4. Check `git status`; preserve unrelated user changes.

Do not create TODO lists, plan markdown, or GitHub issues for project work. Add
discovered work with `bd create ... --deps discovered-from:<current-id>`.

## Repository map

- [Architecture](ARCHITECTURE.md): components, domains, dependency direction.
- [Documentation index](docs/index.md): canonical project knowledge.
- [Product scope](docs/product-specs/walking-slice.md): supported journeys and
  explicit non-goals.
- [Core beliefs](docs/design-docs/core-beliefs.md): system invariants.
- [Adapter model](docs/design-docs/adapter-loading.md): ports and deployment.
- [Frontend](docs/FRONTEND.md): Lit, Effect, semantic HTML, and styling rules.
- [Quality](docs/QUALITY.md): required tests, coverage, and mechanical gates.
- [Security](docs/SECURITY.md): auth, secrets, custody, and threat boundaries.
- [Reliability](docs/RELIABILITY.md): failure semantics and operations.
- [Work tracking](docs/WORK_TRACKING.md): Beads workflow and handoff rules.
- `docs/references/`: source material; do not treat it as executable behavior.

## Architecture rules

- Parse untrusted data once at HTTP, Kafka, environment, and database edges.
- Domain code depends inward only; infrastructure implements typed ports.
- Use integer or exact decimal arithmetic for quantities and money; never float.
- Every matched usage event cites one immutable quoted workload.
- Authorization failures fail closed. Signer events are idempotent.
- Store opaque IDs and hashed credentials. Never log secrets or raw OTPs.
- Enterprise and commercial integrations are optional layers, never core dependencies.

## Frontend rules

- Use Lit web components and Effect 3 stable; no React or meta-framework.
- Use semantic native HTML first and no inline `style` attributes.
- `global.css` owns tokens, themes, typography, and application layout.
- Shadow components consume inherited custom properties and expose `part` /
  `exportparts`; component CSS may only define encapsulated structure.
- TypeScript is strict, immutable, and boundary-decoded. Application failures
  use typed Effect error channels; do not throw in application code.
- Component behavior is tested in real browsers, not jsdom or happy-dom.

## Skills

Read the matching repository-local skill before work in that area:

- `.agents/skills/beads/`: all task tracking and dependency graphs.
- `.agents/skills/effect-fp/`: any TypeScript domain, service, or workflow.
- `.agents/skills/lit-web-apps/`: Lit application and component work.
- `.agents/skills/modern-css/`: any CSS or visual interaction.
- `.agents/skills/semantic-html/`: markup semantics and accessibility. Its
  static-page no-CSS constraint applies only to static documents; application
  presentation follows `docs/FRONTEND.md`.

## Required validation

Run the narrowest relevant checks while iterating and the full Make targets
before closing a bead. Backend and each frontend independently require at least
85% lines, statements, functions, and branches. A change is incomplete when
tests, types, lint, generated contracts, migrations, docs, or Compose smoke
checks disagree with behavior.

Before handoff: update the bead with material findings, close completed beads
with a reason, run `git status`, and report validation evidence. Do not push
unless the user explicitly authorizes it.
