# Architecture at Scale

## App-level layering

```
pages/        route components — orchestrate, no business logic
components/   presentational/leaf — props down, events up, no service access
state/        signals modules + context definitions — plain TS modules are the store
services/     typed API clients behind interfaces — the ONLY place that talks to backends
server/       Hono render layer — stateless, thin
```

- Pages consume services + signals; leaf components stay pure (testable in isolation, reusable in the design system).
- Every service is an interface + implementation. This is what makes backend choice (Node/Go/Rust) a deployment detail:

```ts
export interface ProductApi {
  getProduct(id: string): Promise<Product>;
  search(q: Query): Promise<Product[]>;
}
// impls: local-product-api.ts (in-process, dev/seed) | http-product-api.ts (fetch → any backend)
// Provide via @lit/context on the client; direct import in server route handlers.
```

## Rust / Go backends

Hard constraint: `@lit-labs/ssr` requires a JS runtime — Lit components cannot render inside a Rust/Go process. Three sanctioned shapes:

1. **SSG-heavy (preferred when it fits):** prerender all content routes at build time; Rust/Go (axum, actix, net/http…) serves `dist/client` static files + JSON APIs. **No JS runtime in production at all.** Interactivity comes from hydration + islands.
2. **Render service (BFF):** the Hono app is a small, stateless HTML-rendering tier; Rust/Go services own all business logic and data. Edge/proxy routes `/api/*` → Go/Rust, everything else → render service; the render service's `load()` functions call the Rust/Go APIs via the `http-*` service implementations. Scale it horizontally like any stateless tier; it holds no state beyond module code.
3. **Embedded JS engine (v8go, rusty_v8, QuickJS):** technically possible, operationally painful (engine upgrades, memory, streaming). Not recommended; do not scaffold this.

Decision rule: content/marketing → shape 1; personalized SSR pages with non-JS backends → shape 2; pure dashboards → CSR mode and Rust/Go only ever serves JSON.

## Monorepo & design system

- npm/pnpm workspaces; the lit monorepo itself uses **Wireit** (Lit-team tool) for task orchestration/caching — Turborepo/Nx equally fine.
- Package granularity: `@org/tokens` (CSS custom properties + theme files), `@org/components` (or per-component packages once consumers need independent versioning), `@org/app-*`.
- Components are consumable by ANY stack because they're standard custom elements — the design system outlives app-framework decisions. (Proof points at scale: Material Web, Adobe Spectrum, Shoelace/Web Awesome, Carbon, Red Hat DS — all Lit.)
- Theming contract: components read `var(--org-*)` tokens and expose `part=""` hooks; apps ship token sheets. No component ever hardcodes a brand value.

## Tooling for large codebases

- **`custom-elements.json`** via `@custom-elements-manifest/analyzer`: generated in each component package's build; powers editor completions, API docs, Storybook, and downstream tooling. Treat it as a build artifact and publish it. Working invocation: `custom-elements-manifest analyze --litelement --globs "src/*.ts"` (npm script; the bin is `custom-elements-manifest`).
- **lit-analyzer** (CI) + **ts-lit-plugin** (editor): type-checks bindings *inside* `html\`\`` — `.prop` types, attribute names, unknown tags. Non-negotiable in CI for large teams: it's the only thing type-checking your templates.
- **eslint-plugin-lit + eslint-plugin-wc**: template hygiene rules (no legacy binding syntax, no invalid a11y patterns).
- `HTMLElementTagNameMap` + `HTMLElementEventMap` declarations in every component file — this is what makes a 500-component codebase navigable.

## Conventions that keep large Lit apps sane

- Tag-name prefix per package (`org-button`, `admin-user-table`) — custom element names are a global namespace.
- One element per file; file name = tag name; side-effect `@customElement` registration in the same file; import the file, use the tag.
- If two independently-versioned apps share a page (micro-frontends), registry collisions are the failure mode: keep shared components in one versioned package, or adopt `@lit-labs/scoped-registry-mixin` (nearing graduation) where true isolation is required.
- Events are the public API surface as much as properties: name them like DOM events (`lowercase-kebab`), always `bubbles: true, composed: true` for app-level events, document `detail` types.
- No component reaches into another's shadow root. Ever. If you need it, the child is missing a property, event, or part.
