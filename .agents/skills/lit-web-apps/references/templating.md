# Templating (lit-html)

## Expression positions

```ts
html`
  <div>${childValue}</div>                 <!-- child -->
  <img src=${url} alt="...">               <!-- attribute -->
  <input ?disabled=${isDisabled}>          <!-- boolean attribute (adds/removes) -->
  <my-el .items=${items}></my-el>          <!-- PROPERTY — use for objects/arrays, always -->
  <button @click=${this._onClick}>         <!-- event listener -->
  <div ${ref(this._divRef)}></div>         <!-- element expression (directives only) -->
`;
```

- `.prop=${}` sets JS properties — the only correct way to pass non-string data to components.
- Sentinels: `nothing` (render nothing / remove attribute), `noChange` (skip this part).
- Templates must be well-formed HTML with expressions removed. No expressions in tag names, attribute names, comments, or `<template>` content.
- Tags: `html`, `svg` (fragments inside `<svg>`), `mathml` (Lit 3.2+).
- lit-html works standalone too: `render(html\`...\`, document.body)` — useful for non-component surfaces.

## Built-in directives — complete map

Import from `lit/directives/<name>.js`.

| Directive | Use for |
|---|---|
| `classMap(obj)` | toggle classes from an object (must be the only thing in `class`) |
| `styleMap(obj)` | inline styles from an object |
| `when(cond, trueTpl, falseTpl?)` | ternary, reads better |
| `choose(value, cases, default?)` | switch/case over templates |
| `map(items, fn)` | simple iteration (lazily, no array alloc) |
| `repeat(items, keyFn, tpl)` | **keyed** list diffing — required when items reorder/insert/remove and hold DOM state |
| `join(items, sep)` | interleave separators |
| `range(n)` | numeric iteration |
| `ifDefined(v)` | omit attribute when `undefined` |
| `guard([deps], fn)` | skip re-evaluating an expensive template unless dep **identity** changes |
| `cache(tpl)` | preserve DOM (incl. form state) when switching between templates |
| `keyed(key, tpl)` | force a fresh element instance when key changes (reset state, enter/exit animations) |
| `live(v)` | for `<input .value=${}>` — checks against the *live* DOM value, not last-rendered |
| `ref(refOrCallback)` | get an element reference (`createRef<HTMLInputElement>()`) |
| `until(promise, ...fallbacks)` | render placeholder until a promise resolves (prefer `@lit/task` in components) |
| `asyncAppend` / `asyncReplace` | render AsyncIterables |
| `templateContent`, `unsafeHTML`, `unsafeSVG` | trusted raw content ONLY — never user input |

## Custom directives

Function form for pure transforms; class form for stateful/imperative DOM work:

```ts
import { Directive, directive, type PartInfo } from 'lit/directive.js';

class Highlight extends Directive {
  constructor(partInfo: PartInfo) { super(partInfo); /* validate part type here */ }
  render(text: string, term: string) {
    // SSR runs ONLY render() — keep it DOM-free
    return text; // or a TemplateResult
  }
  // optional: update(part, [text, term]) for direct DOM manipulation; return noChange
}
export const highlight = directive(Highlight);
```

**AsyncDirective** (`lit/async-directive.js`) adds `setValue()` for out-of-band updates plus `disconnected()`/`reconnected()` — the correct home for subscriptions (this is how the signals `watch()` directive works). Always clean up in `disconnected()` and check `this.isConnected`.

## Static templates

```ts
import { html, literal } from 'lit/static-html.js';
const tag = literal`h1`;                       // dev-controlled only
html`<${tag}>Hello</${tag}>`;
```

`literal` values interpolate into template *structure* (dynamic tag names). Changing a static value forces a full re-parse — treat as constant. `unsafeStatic()` only for fully trusted strings.
