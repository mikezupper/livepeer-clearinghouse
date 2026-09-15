# Contributing

Thank you for improving Livepeer Open Clearinghouse. Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Install Python 3.14, UV 0.12.12 or newer, Node 24 or newer, npm, Docker with Compose, and Make.

```sh
uv sync --frozen
cd frontend
npm ci --ignore-scripts
npx playwright install chromium firefox webkit
cd ..
make test
```

Use `make init-env` only when you need the local Compose distribution. Never commit `.env`, OTPs, API keys, RPC credentials, wallets, signer keys, or password files.

## Work and changes

This repository uses Beads as its persistent dependency-aware tracker. Run `bd prime`, claim a ready bead, and record newly discovered work there. Do not create parallel Markdown task lists.

Keep changes focused and preserve the architecture boundaries in [ARCHITECTURE.md](ARCHITECTURE.md). New enterprise behavior belongs behind a versioned contract or typed port. Do not add runtime plugin scanning, hidden PostgreSQL dependencies, or commercial integrations to the core.

Frontend contributions must follow [docs/FRONTEND.md](docs/FRONTEND.md): Lit and Effect, semantic native HTML, no inline styles, global design tokens, and explicit Shadow Parts. Backend code must use exact arithmetic, typed ports, and strict boundary validation.

Product copy follows the hierarchy, canonical terms, help layers, and state
patterns in [docs/product-specs/content-design.md](docs/product-specs/content-design.md).
Keep essential guidance visible, use native `details` only for supplemental
explanations, and reserve tooltips for short term definitions. A contributor
changing a form must verify that every control retains a visible label and that
hint and error IDs are connected with `aria-describedby`. A contributor
changing a state must verify that the message identifies what happened and a
safe next action.

## Tests and review

Every behavior change requires tests. The backend and every frontend codebase must independently remain at or above 85% for lines, statements, functions, and branches. Run the focused test while iterating and `make test` before requesting review. If a generated OpenAPI or custom-elements artifact changes, review the diff rather than accepting it blindly.

A pull request should explain motivation, behavior, trust-boundary impact, validation performed, and any compatibility or deployment change. Commits and pull requests must not include `Co-authored-by` or other co-author attribution trailers.

For frontend copy changes, reviewers also check heading order, landmark and
control names, table captions, destructive-action labels, amount terminology,
one-time-secret guidance, and loading, empty, success, stale, denied, and error
states. Update focused application tests in `frontend/apps/*/src/*.test.ts` and,
when the accessible interaction changes, the browser checks in
`frontend/e2e/clearinghouse.accessibility.spec.ts` or
`frontend/e2e/clearinghouse.journey.spec.ts`. Tests should assert observable
meaning and accessible relationships rather than duplicating whole templates.

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
