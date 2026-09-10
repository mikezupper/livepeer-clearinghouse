# Rendering Modes: SSR, SSG, CSR — one pipeline

`@lit-labs/ssr` renders Lit templates to HTML with **Declarative Shadow DOM** (`<template shadowrootmode="open">`). DSD is Baseline across all browsers (Chrome/Edge 111+, Safari 16.4+, Firefox 123+) — server-rendered pages paint fully **with zero JavaScript**. That single fact powers all three modes:

- **SSR** — run `render()` per request, stream it. Personalized + SEO.
- **SSG** — run the *same* `render()` at build time over the route table, write `.html` files. Content/marketing/docs — crawlable, CDN-servable, no JS runtime in prod.
- **CSR** — skip prerendering; serve the shell; normal client render. Auth-walled apps.

## Server-only document template (src/document.ts)

Normal Lit templates can't render `<!doctype>`, `<head>`, or `<script>`. Server-only templates (the `html` export from `@lit-labs/ssr` itself) can — this is where SEO lives:

```ts
import { html } from '@lit-labs/ssr';                 // server-only html — NOT lit's
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import type { TemplateResult } from 'lit';

export interface PageMeta {
  title: string; description: string;
  canonical?: string; og?: Record<string, string>; jsonLd?: object;
}

// Script-safe JSON: '</script>' inside data must not break out of the data block (see security.md)
const safeJson = (data: unknown) =>
  JSON.stringify(data).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');

// CRITICAL: template bindings are HTML-escaped even inside <script> — and
// browsers/crawlers do NOT entity-decode script content, so a plain
// ${safeJson(data)} binding emits broken &quot;-riddled JSON. Emit the whole
// script element as raw HTML. This is the app's ONE audited unsafeHTML call
// site — injection-safe because safeJson output contains no '<'.
const jsonScript = (attrs: string, data: unknown) =>
  unsafeHTML(`<script ${attrs}>${safeJson(data)}</script>`);

export const document = (meta: PageMeta, body: TemplateResult, data: unknown) => html`
  <!doctype html>
  <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>${meta.title}</title>
      <meta name="description" content=${meta.description}>
      ${meta.canonical ? html`<link rel="canonical" href=${meta.canonical}>` : ''}
      ${Object.entries(meta.og ?? {}).map(([k, v]) => html`<meta property="og:${k}" content=${v}>`)}
      ${meta.jsonLd ? jsonScript('type="application/ld+json"', meta.jsonLd) : ''}
      <!-- FOUC guards must be SCOPED to client-rendered tags. A blanket
           :not(:defined){visibility:hidden} permanently hides every
           prerendered page whose definition never ships to the client. -->
    </head>
    <body>
      ${body}
      ${jsonScript('type="application/json" id="__DATA__"', data)}
      <script type="module" src="/assets/entry-client.js"></script>
    </body>
  </html>
`;
```

Server-only templates cannot carry event handlers or property bindings and are never hydrated — they're the static shell around the hydratable app template (which uses regular `lit` `html`). And note the `unsafeHTML` above: it is load-bearing, not style — `${safeJson(data)}` written directly inside `<script>` renders `&quot;`-escaped JSON that fails to parse on the client.

## SSR: streaming per request (src/entry-server.ts)

```ts
import { Hono } from 'hono';
import { Readable } from 'node:stream';
import { render } from '@lit-labs/ssr';
import { RenderResultReadable } from '@lit-labs/ssr/lib/render-result-readable.js';
import { routes, matchRoute } from './routes.js';
import { document } from './document.js';

export function createApp() {
  const app = new Hono();
  app.get('*', async (c) => {
    const match = matchRoute(new URL(c.req.url));
    if (!match) return c.notFound();
    const data = await match.route.load?.(match.params) ?? null;  // ALL async work happens HERE
    const result = render(document(match.route.meta(data), match.route.template(data), data));
    return c.body(Readable.toWeb(new RenderResultReadable(result)) as ReadableStream, 200, {
      'content-type': 'text/html; charset=utf-8',
    });
  });
  return app;
}
```

- **Streaming** (`RenderResultReadable`) is preferred: lower memory, earlier first byte. `collectResult(result)` → string when you need the whole document (SSG, caching, email).
- Component modules are imported into the **global scope** (one shared `customElements` registry across requests) — the fast, standard path for a long-lived server. VM-sandboxed `renderModule()` exists for per-request isolation but costs module re-evaluation and needs `--experimental-vm-modules`; don't use it unless request isolation is a hard requirement.
- **Server executes only `constructor` + `willUpdate` + `render`.** No DOM access, no fetch, no timers in those paths. Guard browser-only code with `isServer` from `lit`.

## SSG: prerender script (scripts/prerender.ts)

```ts
import { mkdir, writeFile } from 'node:fs/promises';
import { render } from '@lit-labs/ssr';
import { collectResult } from '@lit-labs/ssr/lib/render-result.js';
import { routes } from '../dist/server/routes.js';
import { document } from '../dist/server/document.js';

for (const route of routes.filter((r) => r.mode === 'ssg')) {
  const paths = route.staticPaths ? await route.staticPaths() : [route.path]; // expand :params
  for (const path of paths) {
    const params = route.extractParams(path);
    const data = await route.load?.(params) ?? null;
    const htmlText = await collectResult(render(document(route.meta(data), route.template(data), data)));
    await mkdir(`dist/client${path}`, { recursive: true });
    await writeFile(`dist/client${path}/index.html`, htmlText);
  }
}
```

Output is plain HTML+DSD: serve from a CDN, nginx, or a Go/Rust file server. Pages are interactive after hydration but **readable and crawlable with JS disabled**. Generate `sitemap.xml` from the same route table in this script.

## Hydration (client side)

1. `import '@lit-labs/ssr-client/lit-element-hydrate-support.js'` — **before any `lit` import**. LitElement then detects it was server-rendered (DSD present) and hydrates instead of re-rendering.
2. First client render must use the **same template + same data** as the server → seed state from `__DATA__` before importing components.
3. Hydration is **top-down**: parents hydrate first (they may own children's property bindings).
4. Top-down also means **a parent's `firstUpdated` fires while its children are still hydrating.** Mutating shared state (signals, context) there forces children to render *new* content mid-hydration — "Hydration value mismatch" plus duplicated DOM. Wait for the whole tree first; safe pattern in data-and-state.md § Client-only state.
5. Bindings must match by *value class*, not just data: any part where the server rendered nothing/a string but the client's first render produces a `TemplateResult` (or vice versa) mismatches — the classic case is `@lit/task` (see data-and-state.md).

## Islands / lazy hydration (defer-hydration protocol)

There is no shipped lazy-hydration mixin; the mechanism is the community `defer-hydration` attribute — an element carrying it will not hydrate until the attribute is **removed**:

```ts
// Server: render(template, { deferHydration: true }) stamps defer-hydration on top-level elements,
// or stamp it manually on specific islands in the template: <heavy-widget defer-hydration>

// Client: wake an island on visibility (or interaction, or idle)
const io = new IntersectionObserver(async ([entry]) => {
  if (!entry.isIntersecting) return;
  await import('./heavy-widget.js');                 // load its definition only now
  entry.target.removeAttribute('defer-hydration');   // → hydrates
  io.disconnect();
});
io.observe(document.querySelector('heavy-widget')!);
```

This gives Astro-style islands with ~15 lines of code and no framework. Static SSG page + a couple of deferred interactive islands is the highest-performance shape this stack produces.

## Hard limitations to design around

- **No async component render on the server** — data via route handlers only (see data-and-state.md).
- **Light-DOM components (`createRenderRoot() { return this }`) are not SSR-compatible.** Shadow DOM everywhere.
- DOM shim is minimal: no `querySelector`, no layout, no observers doing real work during SSR. `firstUpdated`/`updated` run client-only — put DOM measurement there.
- `connectedCallback` runs on the server only behind `globalThis.litSsrCallConnectedCallback = true`; even then, no DOM mutation, and events can't mutate parents (streaming).
- Per-class opt-out: components that can't SSR can disable it via `LitElementRenderer` config and render client-only inside a `defer-hydration`/CSR boundary.
