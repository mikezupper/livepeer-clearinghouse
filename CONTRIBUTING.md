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

## Tests and review

Every behavior change requires tests. The backend and every frontend codebase must independently remain at or above 85% for lines, statements, functions, and branches. Run the focused test while iterating and `make test` before requesting review. If a generated OpenAPI or custom-elements artifact changes, review the diff rather than accepting it blindly.

A pull request should explain motivation, behavior, trust-boundary impact, validation performed, and any compatibility or deployment change. Commits and pull requests must not include `Co-authored-by` or other co-author attribution trailers.

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
