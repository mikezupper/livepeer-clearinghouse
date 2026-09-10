# Routing — Platform-Native (URLPattern + Navigation API)

No stable first-party Lit router exists (`@lit-labs/router` is Prototyping-tier and stalled; Vaadin Router is unmaintained). The modern answer is ~100 lines over two platform APIs: **URLPattern** for matching, **Navigation API** for SPA interception (with a small click/popstate fallback where it hasn't shipped). This router is isomorphic — the server matches the same route table.

## Route table (src/routes.ts) — the single source of truth

```ts
import type { TemplateResult } from 'lit';
import { html } from 'lit';

export interface Route<T = unknown> {
  path: string;                                   // URLPattern pathname syntax: '/products/:id'
  mode: 'ssr' | 'ssg' | 'csr';
  load?: (params: Params) => Promise<T>;          // server: awaited pre-render; client: on navigation
  template: (data: T) => TemplateResult;          // renders the page element, data down as properties
  meta: (data: T) => PageMeta;                    // title/description/OG for the document shell
  enter?: () => Promise<unknown>;                 // lazy-load the page element's module
  staticPaths?: () => Promise<string[]>;          // SSG param expansion
}

export const routes: Route[] = [
  {
    path: '/',
    mode: 'ssg',
    enter: () => import('./pages/home-page.js'),
    template: () => html`<home-page></home-page>`,
    meta: () => ({ title: 'Home', description: '…' }),
  },
  {
    path: '/products/:id',
    mode: 'ssr',
    enter: () => import('./pages/product-page.js'),
    load: ({ id }) => api.getProduct(id),
    template: (p) => html`<product-page .product=${p}></product-page>`,
    meta: (p) => ({ title: p.name, description: p.summary, og: { image: p.image } }),
  },
  {
    path: '/admin/*',
    mode: 'csr',
    enter: () => import('./pages/admin-app.js'),
    template: () => html`<admin-app></admin-app>`,
    meta: () => ({ title: 'Admin', description: '' }),
  },
];

export function matchRoute(url: URL) {
  for (const route of routes) {
    const m = new URLPattern({ pathname: route.path }).exec(url);
    if (m) return { route, params: m.pathname.groups as Params };
  }
  return null;
}
```

`enter()` is the code-splitting seam: the page element's module loads only when its route activates. The server imports all page modules up front; the client loads them on demand. Rendering `<product-page>` before its definition loads is fine — custom elements upgrade in place (FOUC-guard with a scoped `product-page:not(:defined)` rule; never blanket).

## Router controller (src/router.ts)

```ts
import type { ReactiveController, ReactiveControllerHost } from 'lit';
import { matchRoute, type Route } from './routes.js';

export class Router implements ReactiveController {
  outlet: unknown;                                 // current page template — render ${this.router.outlet}

  constructor(private host: ReactiveControllerHost & HTMLElement) {
    host.addController(this);
  }

  hostConnected() {
    if ('navigation' in window) {
      // Navigation API: intercept same-origin navigations, keep URL semantics native
      (window as any).navigation.addEventListener('navigate', (e: any) => {
        if (!e.canIntercept || e.hashChange || e.downloadRequest) return;
        const url = new URL(e.destination.url);
        if (!matchRoute(url)) return;              // let the browser handle unknown URLs
        e.intercept({ handler: () => this.#show(url) });
      });
    } else {
      // Fallback: intercept link clicks + popstate
      document.addEventListener('click', this.#onClick);
      window.addEventListener('popstate', () => this.#show(new URL(location.href)));
    }
    this.#show(new URL(location.href), /*initial*/ true);
  }

  async #show(url: URL, initial = false) {
    const match = matchRoute(url);
    if (!match) return;
    await match.route.enter?.();                   // lazy-load page module
    // Initial SSR/SSG load: data already seeded from __DATA__; skip refetch.
    const data = initial ? seededData() : await match.route.load?.(match.params);
    this.outlet = match.route.template(data);
    document.title = match.route.meta(data).title;
    this.host.requestUpdate();
  }

  #onClick = (e: MouseEvent) => {
    const a = (e.composedPath() as Element[]).find((el) => el.localName === 'a') as HTMLAnchorElement | undefined;
    if (!a || a.origin !== location.origin || a.target || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    history.pushState(null, '', a.href);
    this.#show(new URL(a.href));
  };
}
```

```ts
// app-shell.ts
@customElement('app-shell')
export class AppShell extends LitElement {
  private router = new Router(this);
  render() {
    return html`<app-nav></app-nav><main>${this.router.outlet}</main>`;
  }
}
```

## Rules

- **Plain `<a href>` everywhere.** The router intercepts; no `<router-link>` component. Progressive enhancement is free — links work before hydration.
- Scroll restoration: Navigation API handles it via `e.intercept({ scroll: 'after-transition' })`; in the fallback, restore manually on popstate.
- Nested routing: a page component instantiates its own `Router`-like `Routes` sub-controller against a sub-table, matching on the remainder. Keep it flat until genuinely needed.
- 404: server returns a rendered not-found page with status 404; client `#show` falls through to a not-found template.
- View transitions: wrap `this.host.requestUpdate()` in `document.startViewTransition()` when available — free animated navigation.
- URLPattern is broadly available (Node 24+ has it too; polyfill `urlpattern-polyfill` only if targeting old runtimes).
