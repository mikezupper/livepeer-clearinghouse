# Data & State

## The ladder (strict — pick the lowest rung that works)

1. **Component-local** → reactive properties (`@state`, `@property`)
2. **Async fetch tied to a component** → `@lit/task`
3. **Cross-tree services, theme, session, config** → `@lit/context`
4. **Shared reactive app state** (cart, filters, live data) → `@lit-labs/signals`

## Signals — shared reactive state (@lit-labs/signals)

Built on the TC39 Signals proposal polyfill (`signal-polyfill`) — the API surface is the future standard.

```ts
// src/state/cart.ts — a plain module IS the store; no framework machinery
import { signal, computed } from '@lit-labs/signals';

export const items = signal<CartItem[]>([]);
export const count = computed(() => items.get().length);
export const total = computed(() => items.get().reduce((s, i) => s + i.price, 0));

export function addItem(item: CartItem) {
  items.set([...items.get(), item]);   // immutable set — same rule as reactive properties
}
```

Three consumption levels, coarse → fine:

```ts
import { SignalWatcher, watch, html as signalHtml } from '@lit-labs/signals';

// 1. SignalWatcher mixin: any signal read during render() auto-triggers host updates
class CartBadge extends SignalWatcher(LitElement) {
  render() { return html`<span>${count.get()}</span>`; }
}

// 2. watch() directive: pinpoint per-binding DOM update, no full component render
render() { return html`<span>${watch(count)}</span>`; }

// 3. signals-aware html tag: auto-wraps signal values in watch()
render() { return signalHtml`<span>${count}</span>`; }
```

Default to **SignalWatcher** — Lit already updates only changed bindings, so `watch()` is a targeted optimization for hot paths, not a default.

Driving **imperative work** (canvas, charts, third-party widgets) from signals: there is no `effect()` export — use the polyfill's watcher primitive, batched through rAF:

```ts
import { Signal } from '@lit-labs/signals';   // re-exports ALL of signal-polyfill

export function subscribe(signals: Signal.State<any>[] | Signal.Computed<any>[], onChange: () => void) {
  const watcher = new Signal.subtle.Watcher(() => {
    queueMicrotask(() => { watcher.watch(); onChange(); });  // re-arm, then react
  });
  const probes = signals.map((s) => new Signal.Computed(() => s.get()));
  probes.forEach((p) => { watcher.watch(p); p.get(); });
  return () => probes.forEach((p) => watcher.unwatch(p));
}
```

Gotchas:
- Exactly one `signal-polyfill` copy in the graph (`npm ls signal-polyfill`; `npm dedupe`). Duplicates silently partition reactivity. Note `@lit-labs/signals` **re-exports all of signal-polyfill** (`Signal` included) — never add a direct `signal-polyfill` dependency; import everything from `@lit-labs/signals`.
- SSR: signals read fine during server render (current value). Initialize signal state from serialized route data **before** hydration so server and client renders match.
- Labs status: pin the minor version; the API tracks the TC39 proposal.

## Context — services & DI (@lit/context, stable)

Uses the W3C community Context protocol (composed DOM events) — works across shadow roots and even with non-Lit elements.

```ts
// src/state/session.ts
import { createContext } from '@lit/context';
export interface Session { user: User | null; }
export const sessionContext = createContext<Session>(Symbol('session'));
```

```ts
import { provide, consume } from '@lit/context';

class AppShell extends LitElement {
  @provide({ context: sessionContext }) session: Session = { user: null };
}

class UserMenu extends LitElement {
  // subscribe: true → re-render when the provider updates the value
  @consume({ context: sessionContext, subscribe: true }) session?: Session;
}
```

Controller forms (`ContextProvider` / `ContextConsumer` with `.setValue()`) exist for imperative cases. Use context for **identity and services** (API clients, theme, locale, session) — put *changing app data* in signals, and if a context value must be reactive, make it `{ items: Signal<T> }` (stable identity, reactive interior).

## Async data — @lit/task (stable)

Race-condition-safe async with declarative status rendering. Re-runs when args change (shallow compare); only the latest invocation lands.

```ts
import { Task } from '@lit/task';

class ProductList extends LitElement {
  @property() category = '';

  private _products = new Task(this, {
    args: () => [this.category] as const,          // `as const` → typed tuple
    task: async ([category], { signal }) => {       // AbortSignal built in
      const res = await fetch(`/api/products?cat=${category}`, { signal });
      if (!res.ok) throw new Error(res.statusText);
      return res.json() as Promise<Product[]>;
    },
  });

  render() {
    return this._products.render({
      pending: () => html`<sk-list-skeleton></sk-list-skeleton>`,
      complete: (products) => html`${repeat(products, (p) => p.id, (p) => html`<product-card .product=${p}></product-card>`)}`,
      error: (e) => html`<error-panel .error=${e}></error-panel>`,
    });
  }
}
```

`autoRun: false` + `.run()` for manual triggering (e.g. submit buttons).

**SSR + Task hydration trap:** a Task never runs during server render — the server renders the **INITIAL** state (nothing, unless you provide `initial`). But by the client's first (hydrating) render the task is already **PENDING**. If those two render differently, hydration fails with "Hydration value mismatch". Give `initial` and `pending` the *same* template function:

```ts
#skeleton = () => html`<sk-list-skeleton></sk-list-skeleton>`;
render() {
  return this._products.render({
    initial: this.#skeleton,   // server renders this…
    pending: this.#skeleton,   // …client's first render matches it exactly
    complete: (products) => html`…`,
    error: (e) => html`…`,
  });
}
```

## The SSR → client data-flow convention (load-bearing)

Server render is synchronous — components cannot fetch. The pipeline is:

1. **Route handler fetches** everything the page needs (in parallel) via the services layer.
2. Data flows **down as properties** in the server-rendered template: `html\`<product-page .products=${products}>\``.
3. The document template **serializes** the same data: `<script type="application/json" id="__DATA__">…</script>`.
4. Client entry **reads `__DATA__` and seeds signals/properties before hydration** — hydration requires the client's first render to match the server's template + data exactly.
5. Post-hydration interactivity (pagination, refresh, mutations) uses `@lit/task` / `fetch` normally.

```ts
// entry-client.ts (after hydrate-support import)
const seed = JSON.parse(document.getElementById('__DATA__')!.textContent!);
hydrateAppState(seed);   // sets signals; providers pick up values
import('./app-shell.js');
```

Rule of thumb: SSR data answers "what does first paint need?" — everything else is a client-side Task.

## Client-only state (localStorage sessions, preferences) and hydration

The server cannot see `localStorage` — it always renders the signed-out/default view. The first client render must match it **exactly**, so client-only state must NOT be read at module scope or restored during hydration:

```ts
// ❌ first client render sees the restored session → differs from server → mismatch + duplicated DOM
export const session = signal<Session | null>(readFromLocalStorage());

// ✅ start null on BOTH sides; restore after the WHOLE tree has hydrated
export const session = signal<Session | null>(null);
export function restoreSession() {
  const raw = localStorage.getItem(KEY);
  if (raw !== null) session.set(JSON.parse(raw));
}
```

And "after hydration" means the whole tree, not just the shell: hydration is top-down, so the shell's `firstUpdated` fires while its children are still hydrating — mutating a shared signal there corrupts *their* hydration. Await the signal-reading children first:

```ts
// app-shell.ts
override async firstUpdated() {
  const slotted = this.shadowRoot?.querySelector('slot')?.assignedElements() ?? [];
  const children = [...this.shadowRoot!.querySelectorAll('nav-bar, cart-drawer'), ...slotted];
  await Promise.all(children.map((el) => (el as Partial<LitElement>).updateComplete ?? Promise.resolve()));
  restoreSession();               // now the reactive path re-renders them normally
}
```

Symptom key: "Hydration value mismatch: Unexpected TemplateResult rendered to part" + doubled UI (e.g. two headers, one signed-in and one signed-out) = some state diverged between the server render and the first client render. Find what was restored too early.
