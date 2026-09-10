# Component Authoring

## Anatomy

```ts
import { LitElement, html, css, type PropertyValues } from 'lit';
import { customElement, property, state, query } from 'lit/decorators.js';

@customElement('user-card')
export class UserCard extends LitElement {
  static styles = css`
    :host { display: block; }
    :host([highlighted]) { outline: 2px solid var(--accent, #0b57d0); }
  `;

  // Public API: attribute-backed, part of the element's contract
  @property() name = '';
  @property({ type: Number }) score = 0;
  @property({ type: Boolean, reflect: true }) highlighted = false;

  // Internal state: no attribute, not part of the API
  @state() private _expanded = false;

  @query('#details') private _details!: HTMLElement;

  // Derived state goes here, NOT in render()
  private _tier = '';
  willUpdate(changed: PropertyValues<this>) {
    if (changed.has('score')) this._tier = this.score > 90 ? 'gold' : 'standard';
  }

  render() {
    return html`
      <h2>${this.name} (${this._tier})</h2>
      <button @click=${() => (this._expanded = !this._expanded)}>toggle</button>
      <div id="details" ?hidden=${!this._expanded}><slot></slot></div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap { 'user-card': UserCard; }
}
```

Always add the `HTMLElementTagNameMap` entry — it types `querySelector`, `createElement`, and lit-analyzer checks.

## Reactive properties

Options for `@property({...})`:
- `type`: String | Number | Boolean | Object | Array — hint for the default attribute converter
- `attribute`: `false` (no attribute), or a string to rename (`attribute: 'user-name'`)
- `reflect: true`: property → attribute sync (for styling hooks / a11y). Never reflect objects/arrays.
- `converter: { fromAttribute, toAttribute }` for custom serialization
- `hasChanged(newVal, oldVal)`: custom dirty check (default: `!==`)
- `useDefault: true` (Lit 3.3+): default value isn't treated as a change and isn't reflected

Rules:
- Boolean attributes: presence = true, so the default **must** be `false`.
- Mutation doesn't trigger updates: `this.items.push(x)` does nothing. Reassign immutably or call `this.requestUpdate()`.
- Plain JS (no decorators): `static properties = { name: {} }` + initialize in `constructor`.

## Update lifecycle (async, batched at microtask timing)

```
property set → hasChanged() → requestUpdate()
  → shouldUpdate(changed)     // gate the whole update (rare)
  → willUpdate(changed)       // compute derived values — safe on SERVER too
  → update() → render()       // render() must be pure
  → firstUpdated(changed)     // once; first DOM access point
  → updated(changed)          // after every render; DOM measurement
  → updateComplete resolves   // await before asserting/measuring
```

- **Server executes only `constructor`, `willUpdate`, `render`** (plus `connectedCallback` if opted in). Never touch DOM or browser APIs in these three.
- Typing note: `PropertyValues<this>` only covers **public** properties — `changed.has('_privateState')` is a type error for private `@state` fields. Fall back to untyped `PropertyValues` when checking private state keys.
- Await children: `async getUpdateComplete() { await super.getUpdateComplete(); await this._child.updateComplete; return true; }`
- Standard callbacks (`connectedCallback` etc.) must call `super.xxx()`.

## Reactive controllers — the primary reuse primitive

Package logic + lifecycle + state, usable by any host component:

```ts
import type { ReactiveController, ReactiveControllerHost } from 'lit';

export class ClockController implements ReactiveController {
  value = new Date();
  #id?: number;
  constructor(private host: ReactiveControllerHost, private interval = 1000) {
    host.addController(this);
  }
  hostConnected() {
    this.#id = setInterval(() => { this.value = new Date(); this.host.requestUpdate(); }, this.interval);
  }
  hostDisconnected() { clearInterval(this.#id); }
  // also available: hostUpdate() (pre-render DOM reads), hostUpdated() (post-render)
}
// usage: private clock = new ClockController(this);  →  render: ${this.clock.value}
```

Controllers compose (pass the host through), pair with directives, and are how `@lit/task`, `@lit/context`, the router, and `@lit-labs/observers` (Resize/Mutation/Intersection/PerformanceObserver wrappers) all work.

**Choose:** controller for "has-a" behavior (default) · mixin for "is-a" additions that must add public API/properties · plain composition for UI structure.

## Mixins

```ts
type Constructor<T = {}> = new (...args: any[]) => T;
export const Loggable = <T extends Constructor<LitElement>>(Base: T) =>
  class extends Base {
    connectedCallback() { super.connectedCallback(); console.log(this.tagName, 'connected'); }
  };
// class MyEl extends Loggable(LitElement) {}
```

## Shadow DOM, slots, events

- Open shadow root by default. Options: `static shadowRootOptions = { ...LitElement.shadowRootOptions, delegatesFocus: true };`
- Slots: `<slot></slot>`, named `<slot name="header">`, fallback content inside. Query with `@queryAssignedElements({ slot: 'header', flatten: true })`. React to `@slotchange`.
- Light-DOM rendering (`createRenderRoot() { return this; }`) loses style scoping AND **is not supported by SSR** — avoid in this stack.
- Events up, properties down. Cross shadow boundaries deliberately:

```ts
this.dispatchEvent(new CustomEvent('item-selected', {
  detail: { id }, bubbles: true, composed: true,
}));
```

Type custom events globally:

```ts
declare global { interface HTMLElementEventMap { 'item-selected': CustomEvent<{ id: string }>; } }
```

## Styling & theming

- `static styles = css\`...\`` — constructable stylesheets, parsed once, shared across all instances. Fastest path; use it always.
- `css` only interpolates other `css` results and numbers (injection-safe). `unsafeCSS()` for trusted constants only.
- Scoping selectors: `:host`, `:host([attr])`, `::slotted(li)`.
  - Slotted (light-DOM) children are reachable **only** via `::slotted()` — ordinary child/descendant combinators in shadow styles (`main > *`) match shadow children and silently skip slotted content. Classic symptom: a slotted page ignores the shell's grid placement (`main > * { grid-column: content }` needs a matching `slot::slotted(*) { grid-column: content }`).
- **Theming contract** for a design system:
  - Inherited CSS custom properties pierce shadow roots: components consume `var(--app-surface, #fff)`; apps set tokens at `:root`.
  - Expose internals explicitly: `<div part="label">` → outside styles via `my-el::part(label)`.
  - Stateful styling: prefer `ElementInternals` `:state()` or reflected attributes (`:host([open])`).
- Dynamic inline styling in templates: `classMap({ active: this.on })`, `styleMap({ width: \`${w}px\` })`.
