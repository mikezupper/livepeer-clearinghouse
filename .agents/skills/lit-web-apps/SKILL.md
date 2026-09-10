---
name: lit-web-apps
description: Build complete, production-grade web applications using Lit web components — no meta-framework, no React/Next.js/Vue. Covers highly reactive components (signals), SSR/SSG/CSR from one pipeline, routing on native platform APIs, TypeScript + Vite + Vitest, localization, and large-scale architecture. Use whenever creating or modifying a Lit application, component library, or SSR server. Do NOT use for integrating Lit with other frameworks.
---

# Building Web Apps with Lit

Lit is the entire framework. Every component is a standard custom element; the platform (Shadow DOM, Declarative Shadow DOM, URLPattern, Navigation API, Context protocol, TC39 Signals) provides what meta-frameworks reinvent. This skill produces apps that server-render by default, hydrate on the client, and scale to large codebases — with React/Next.js-class capability and none of their runtime.

**Scope guard:** This skill is for pure-Lit applications. Never reach for `@lit/react`, `@lit-labs/nextjs`, or any framework wrapper. If asked to integrate Lit into another framework, this skill does not apply.

## The stack (pinned decisions — do not relitigate)

| Concern | Choice | Status |
|---|---|---|
| Components | `lit` ^3.3.0 | Stable |
| Language | TypeScript strict, **experimental decorators** | Official recommendation |
| Local state | Reactive properties (`@property`, `@state`) | Stable |
| Shared/app state | `@lit-labs/signals` (TC39 Signals polyfill) | Labs — API tracks the TC39 proposal |
| Services / DI | `@lit/context` | Graduated, stable |
| Async data | `@lit/task` | Graduated, stable |
| SSR / SSG | `@lit-labs/ssr` + `@lit-labs/ssr-client` | Labs — production-used, actively developed |
| Server | Hono (`@hono/node-server`) — thin, stateless render layer | See references/architecture.md for Rust/Go backends |
| Routing | Hand-rolled reactive controller on **URLPattern + Navigation API** | Platform-native; canonical code in references/routing.md |
| Build/dev | Vite (official `lit-ts` template lineage) | Stable |
| Testing | Vitest 4 browser mode (Playwright provider) + `@open-wc/testing` | Stable |
| i18n | `@lit/localize` | Stable |
| Large lists | `@lit-labs/virtualizer` | Labs |
| Forms | Form-associated custom elements via `ElementInternals` | Platform-native |
| Overlays | Popover API + `<dialog>` (top layer — no portals) | Platform-native |
| Animation | `@lit-labs/motion` + View Transitions | Labs / platform |

## Golden rules

1. **tsconfig is non-negotiable:** `"experimentalDecorators": true`, `"useDefineForClassFields": false`. With standard class-field semantics, fields shadow Lit's reactive accessors and reactivity silently dies.
2. **No async work during server render.** Component rendering on the server is synchronous (`constructor` → `willUpdate` → `render` only). Fetch data in the route handler, pass it down as properties. Client-side follow-up data uses `@lit/task`.
3. **Hydration import order is a hard constraint:** `import '@lit-labs/ssr-client/lit-element-hydrate-support.js'` must execute **before any `lit` import** in the client entry. Violating this breaks hydration silently. (Pure-CSR apps skip this import entirely — their scaffold is just `index.html` + a plain entry module; no `entry-server`/`document`/prerender.)
4. **One route table, three render modes.** Each route declares `mode: 'ssr' | 'ssg' | 'csr'`. SSR streams per request; SSG runs the same renderer at build time; CSR skips prerendering. Same components, same templates.
5. **State has a strict ladder.** Component-local → reactive properties. Async fetch → `@lit/task`. Cross-tree services/theme/session → `@lit/context`. Shared reactive app state → signals. Never prop-drill what context should carry; never put local state in a signal.
6. **Immutability for reactive data.** Mutating an array/object bound to a reactive property does not trigger updates. Reassign (`this.items = [...this.items, x]`) or call `this.requestUpdate()` deliberately.
7. **Compute derived state in `willUpdate()`**, never in `render()`. Keep `render()` a pure function of properties/signals.
8. **Code-split by route.** Custom elements upgrade lazily by design: render `<my-page>` markup immediately, `import('./my-page.js')` in the route's `enter()`. FOUC-guard pending CLIENT-rendered elements with a **scoped** rule (`my-page:not(:defined) { visibility: hidden }`). NEVER use blanket `:not(:defined) { visibility: hidden }` in an SSR/SSG app — prerendered pages are fully painted via DSD but their definitions intentionally never ship, so a blanket rule hides the entire site permanently.
9. **Styles live in `static styles = css\`...\``** (constructable stylesheets, shared across instances). Theme through inherited CSS custom properties and `::part()`. Never per-instance `<style>` tags.
10. **Test in a real browser, always.** Shadow DOM + custom elements need it. `await el.updateComplete` before every assertion. No jsdom/happy-dom.

## Project scaffold

```
my-app/
├── index.html                  # CSR/dev shell
├── vite.config.ts
├── tsconfig.json
├── server/
│   ├── dev.ts                  # Vite middleware-mode dev server
│   └── prod.ts                 # Hono prod server (streams SSR, serves dist/client)
├── scripts/
│   └── prerender.ts            # SSG: renders 'ssg' routes to static HTML
└── src/
    ├── entry-client.ts         # hydrate-support FIRST, then app shell
    ├── entry-server.ts         # createApp(): Hono app + document template
    ├── routes.ts               # single route table (shared client/server/prerender)
    ├── document.ts             # server-only html`` document shell (head, meta, SEO)
    ├── state/                  # signals + context definitions
    ├── services/               # typed API clients (swappable: local | Go | Rust)
    ├── components/             # shared/leaf components
    └── pages/                  # route components (one per route, lazy-loaded)
```

## Decision tables

**Which rendering mode per route?**
- Marketing/docs/content, data known at build → `ssg` (Rust/Go/CDN can serve it — no JS runtime needed in prod)
- Personalized or per-request data, SEO matters → `ssr`
- Behind auth, no SEO value (dashboards, admin) → `csr`

**Which list directive?**
- Items reorder/insert/remove with state → `repeat(items, keyFn, tpl)`
- Append-only or full re-render is cheap → `map()` / plain array
- Expensive template, deps rarely change → wrap in `guard([deps], fn)`
- Swap between templates, preserve DOM state → `cache()`
- Force fresh element instance on key change → `keyed()`
- 10k+ rows → `@lit-labs/virtualizer`

**Which composition primitive?**
- Reusable stateful logic tied to lifecycle → reactive controller (default choice)
- Needs to add public API/properties to the element → class mixin
- UI structure → component composition (properties down, events up)

## References (read on demand)

- `references/project-setup.md` — tsconfig, Vite config, dev/prod servers, package.json, build pipeline
- `references/components.md` — reactive properties, lifecycle, controllers, mixins, shadow DOM, styling/theming, events
- `references/templating.md` — expression types, all built-in directives, custom/async directives, static templates
- `references/data-and-state.md` — signals, context, task; the SSR→client data-flow convention
- `references/rendering-modes.md` — SSR streaming, SSG prerendering, hydration, defer-hydration islands, server-only templates, SEO
- `references/routing.md` — canonical URLPattern + Navigation API router controller, server-side matching, lazy routes
- `references/testing.md` — Vitest browser mode setup, fixtures, shadow DOM assertions, a11y, SSR tests
- `references/performance.md` — perf directives, virtualizer, template compiler, bundle optimization, lazy definition
- `references/localization.md` — @lit/localize runtime vs transform modes, XLIFF workflow
- `references/architecture.md` — scaling patterns, monorepos, design systems, lint tooling, Rust/Go backend integration
- `references/forms.md` — form-associated custom elements, ElementInternals, validation, `:state()`, a11y
- `references/interaction.md` — Popover/dialog top-layer overlays, @lit-labs/motion, View Transitions, observers
- `references/security.md` — unsafe-API rules, Trusted Types, script-safe state serialization, CSP
