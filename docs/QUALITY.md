# Quality policy

Correctness is enforced at the smallest independently deployable boundary.

## Required gates

- Python formatting, Ruff, strict mypy, backend unit/integration/contract tests, and generated OpenAPI drift.
- TypeScript, ESLint, Lit Analyzer, semantic-source policy, Stylelint, custom-elements drift, and production Vite builds.
- Chromium user/admin journeys, automated WCAG checks, responsive dark/light visual baselines, and Firefox/WebKit smoke tests.
- Compose model validation and Docker builds for core, edge, and the pinned remote signer.
- At least 85% lines, statements, functions, and branches independently for the backend and every frontend codebase.

Run `make test` for the complete local software gate and `make build` for image construction. Funded remote-signer validation is environment-specific and begins with `make signer-preflight`.

Billing qualification is ad hoc and intentionally excluded from CI. Run
`make qualify-billing-plan` first, `make qualify-billing-controlled` for the
zero-spend deterministic scenarios, `make qualify-billing-broker` for actual
Redpanda replay, and `QUAL_EXECUTE=true make
qualify-billing-run CASES=<explicit-list>` for selected live cases. Aggregate,
per-case, duration, unit-price, orchestrator, freshness, and input guards all
fail closed. Live-network results are evidence, not deterministic fixtures; see
[the runbook](operations/billing-qualification.md).

## Test principles

Use deterministic clocks and secret factories. Test at typed ports, not implementation-private calls, except for strict decoder edge cases. Database tests use real temporary SQLite files. SDK compatibility tests execute the local `livepeer-python-gateway` checkout. Browser tests exercise production builds and semantic roles.

Every production defect receives a regression test. Coverage denominators prevent accidental code omission or test configuration shrinkage; deliberate architecture reduction updates those floors with review.
