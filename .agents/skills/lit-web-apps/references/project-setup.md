# Project Setup

## Dependencies

```jsonc
// package.json (essentials)
{
  "type": "module",
  "scripts": {
    "dev": "tsx server/dev.ts",
    "build": "vite build --outDir dist/client && vite build --ssr src/entry-server.ts --outDir dist/server && tsx scripts/prerender.ts",
    "start": "node dist/server/prod.js",
    "test": "vitest",
    "lint": "lit-analyzer src && eslint ."
  },
  "dependencies": {
    "lit": "^3.3.0",
    "@lit/context": "^1.1.6",
    "@lit/task": "^1.0.3",
    "@lit-labs/signals": "^0.3.0",
    "@lit-labs/ssr": "^4.1.0",
    "@lit-labs/ssr-client": "^1.1.8",
    "hono": "latest",
    "@hono/node-server": "latest"
  },
  "devDependencies": {
    "vite": "latest",
    "vitest": "^4",
    "@vitest/browser": "^4",
    "playwright": "latest",
    "@open-wc/testing": "latest",
    "typescript": "^5.5",
    "tsx": "latest",
    "lit-analyzer": "latest",
    "eslint-plugin-lit": "latest"
  }
}
```

Run `npm ls signal-polyfill` after install — **exactly one copy** must exist or the signal graph partitions (`npm dedupe` fixes it).

## tsconfig.json

```jsonc
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    // BOTH required for Lit decorators — never change these two:
    "experimentalDecorators": true,
    "useDefineForClassFields": false,
    "plugins": [{ "name": "ts-lit-plugin", "strict": true }]
  }
}
```

Why: Lit officially recommends experimental decorators for production (smaller emitted code than TC39 standard decorators, no `accessor` keyword needed). `useDefineForClassFields: false` is critical with ES2022+ targets — "define" semantics make class fields shadow Lit's generated reactive accessors, silently killing reactivity.

(If a team mandates standard decorators: TS 5.2+, flip both flags, and every decorated field needs `accessor`: `@property() accessor name = ''`. Do not mix modes.)

## vite.config.ts

```ts
import { defineConfig } from 'vite';

export default defineConfig({
  build: { target: 'es2022' },
  // No Lit plugin exists or is needed — Lit is plain ES modules.
  // Server-side: keep `lit` and @lit-labs/ssr* EXTERNAL in SSR builds so Node's
  // export-condition resolution picks the node build (which installs the DOM shim).
  ssr: {
    external: ['lit', '@lit-labs/ssr', '@lit-labs/ssr-client', '@lit/context', '@lit/task'],
  },
});
```

Vite dev mode automatically resolves Lit's `development` export condition → unminified build with dev warnings. Production build gets the minified build automatically.

## Dev server (server/dev.ts) — Vite middleware mode + SSR

There is no official Lit Vite SSR plugin; this wiring is the canonical pattern:

```ts
import http from 'node:http';
import { createServer as createViteServer } from 'vite';
import { getRequestListener } from '@hono/node-server';

const vite = await createViteServer({
  server: { middlewareMode: true },
  appType: 'custom',
});

const ssrHandler = getRequestListener(async (req) => {
  // Re-loaded each request in dev → HMR for server-rendered output
  const { createApp } = await vite.ssrLoadModule('/src/entry-server.ts');
  return createApp().fetch(req);
});

http
  .createServer((req, res) => {
    vite.middlewares(req, res, () => ssrHandler(req, res)); // Vite serves assets; SSR handles the rest
  })
  .listen(5173, () => console.log('dev: http://localhost:5173'));
```

## Prod server (server/prod.ts)

```ts
import { serve } from '@hono/node-server';
import { serveStatic } from '@hono/node-server/serve-static';
import { Hono } from 'hono';
import { createApp } from '../dist/server/entry-server.js';

const app = new Hono();
app.use('/assets/*', serveStatic({ root: './dist/client' })); // hashed, immutable
// SSG output in dist/client/*.html is best served by a CDN or Go/Rust file server
// in front of this process; this Node process only handles `ssr`-mode routes.
app.route('/', createApp());
serve({ fetch: app.fetch, port: 3000 });
```

## Client entry (src/entry-client.ts)

```ts
// ORDER IS LOAD-BEARING: hydrate-support patches LitElement and MUST run
// before any module that imports 'lit'.
import '@lit-labs/ssr-client/lit-element-hydrate-support.js';
import './app-shell.js';
```

For `csr`-only apps, drop the hydrate-support import entirely.

## index.html (dev / CSR shell)

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      /* FOUC guard for CLIENT-rendered roots only — scoped, never blanket:
         a blanket :not(:defined){visibility:hidden} permanently hides
         SSR/SSG-prerendered elements whose definitions never ship. */
      app-shell:not(:defined) { visibility: hidden; }
    </style>
  </head>
  <body>
    <app-shell></app-shell>
    <script type="module" src="/src/entry-client.ts"></script>
  </body>
</html>
```

In SSR/SSG, this file is replaced by the server-only document template (`src/document.ts` — see references/rendering-modes.md), which emits the same script tag plus serialized route data.

## Production build notes

- `vite build` (Rollup underneath) matches Lit's official production guidance: bundle, Terser-class minification, modern output, asset hashing.
- Optional micro-optimization: minify HTML inside `html\`\`` literals with `@lit-labs/rollup-plugin-minify-html-literals` (standard minifiers skip template strings).
- Optional (measurable wins on template-heavy apps): `@lit-labs/compiler` precompiles templates at build time — up to ~45% faster first render, ~+5% bundle. Rollup/production only, does not run in Vite dev. See references/performance.md.
