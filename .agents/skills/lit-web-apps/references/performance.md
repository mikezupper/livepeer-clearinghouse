# Performance at Scale

Lit's baseline is already strong: ~5 KiB core, no virtual DOM, updates touch only changed bindings. Performance work is therefore targeted, not architectural.

## Render-path discipline

- `render()` pure; derived values in `willUpdate()`; gate rare full-update skips with `shouldUpdate()`.
- `repeat(items, keyFn, tpl)` whenever list items reorder — unkeyed lists thrash DOM state.
- `guard([deps], fn)` around expensive template computation; re-runs only on dep **identity** change (pairs perfectly with immutable updates).
- `cache()` when toggling between templates that hold form/scroll state.
- `live()` on controlled inputs to avoid fighting user typing.
- Signals `watch()` directive for genuinely hot bindings (per-binding update, skips the component render entirely). Measure first — Lit's default binding updates are already cheap.
- Batch DOM reads in `hostUpdate()`/`updated()`, never in render. Custom scheduling (e.g. align to rAF) via `scheduleUpdate()` override.

## Large lists — @lit-labs/virtualizer

```ts
import { virtualize } from '@lit-labs/virtualizer/virtualize.js';

render() {
  return html`<ul>${virtualize({
    items: this.rows,                       // 10k+ items fine
    renderItem: (r) => html`<row-item .row=${r}></row-item>`,
  })}</ul>`;
}
```

Also available as `<lit-virtualizer>` element; grid + flow layouts; `scroller` mode makes the virtualizer itself the scroll container. Labs status ("late prerelease") but it is the official answer for big lists.

## Loading strategy

- **Route-level splitting is the default** (router `enter()` → dynamic import). Vite chunks automatically.
- Below-the-fold / interaction-gated components: render the tag, import the definition on IntersectionObserver/interaction, exactly like the islands pattern (rendering-modes.md). Scoped `x-el:not(:defined) { visibility: hidden }` or skeleton styles prevent FOUC (blanket `:not(:defined)` rules hide prerendered DSD content permanently — scope to client-rendered tags only); `customElements.whenDefined('x-el')` when code must wait.
- Preload the next likely route on hover/visible links: `link.addEventListener('pointerenter', () => route.enter?.(), { once: true })`.
- SSG + deferred islands is the fastest page shape this stack produces — static HTML paint, zero JS until an island wakes.

## Build-time optimizations

- **@lit-labs/compiler** (optional, Prototyping tier): TypeScript transformer that precompiles `html\`\`` templates into prepared `CompiledTemplate`s — measured up to ~45% faster first render, ~21% faster updates, ~+5% gzip. Rollup/production builds only (TS transformer via `@rollup/plugin-typescript`); does not run in Vite dev. Adopt when template-prep shows in first-render profiles.
- `@lit-labs/rollup-plugin-minify-html-literals` — standard minifiers skip HTML inside template strings.
- Ship modern (ES2021+): Lit publishes modern code; don't down-compile.
- Lit dev/prod dual builds resolve automatically via export conditions (Vite dev → `development` condition with warnings; build → minified prod).

## Measurement checklist before optimizing

1. Profile first render: is time in template prep (→ compiler), data (→ move to route `load`), or component count (→ islands/virtualizer)?
2. Update jank: look for unkeyed `map()` on reordering lists, object identity churn defeating `guard`/`hasChanged`, work in `render()`.
3. Bundle: route chunks should dominate; a monolithic chunk means `enter()` imports are being statically imported somewhere (check for eager top-level imports of page modules in shared files).
