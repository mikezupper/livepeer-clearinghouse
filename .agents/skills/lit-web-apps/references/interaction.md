# Interaction, Motion & Overlays

The platform now covers what React apps need portal/animation libraries for. Use these before reaching for any dependency.

## Overlays: Popover API + `<dialog>` — no portals, ever

Both render in the **top layer**, which escapes shadow DOM, `overflow: hidden`, and `z-index` stacking entirely. This is why Lit apps never need portal hacks:

```ts
render() {
  return html`
    <button popovertarget="menu">Menu</button>
    <div id="menu" popover @toggle=${this.#onToggle}>…</div>   <!-- light-dismiss, esc, focus -->

    <dialog ${ref(this.#dialog)} @close=${this.#onClose}>…</dialog>
  `;
}
open() { this.#dialog.value?.showModal(); }   // modal: inert background, focus trap built in
```

- `popover` (auto) for menus/tooltips/toasts: light-dismiss + esc for free; `popover="manual"` for persistent toasts.
- `<dialog>.showModal()` for modals: native focus trapping, `::backdrop` styling, `closedby="any"` for light-dismiss.
- Position anchored overlays with **CSS anchor positioning** (`anchor-name` / `position-anchor` / `position-area`) where supported; fallback to manual positioning in `updated()` with a ResizeObserver.

## Motion — @lit-labs/motion (Labs, 1.1.0)

`animate()` directive: FLIP-animates an element when its layout changes between renders, plus enter/exit animations — the "framer-motion basics" without the library:

```ts
import { animate, fadeIn, fadeOut } from '@lit-labs/motion';

render() {
  return html`${repeat(this.items, (i) => i.id, (i) => html`
    <li ${animate({
      keyframeOptions: { duration: 250, easing: 'ease-out' },
      in: fadeIn, out: fadeOut,                    // enter/exit
    })}>${i.label}</li>`)}`;
}
```

- Animates position/size/opacity/color across renders automatically (measures before/after, plays the inversion).
- `AnimateController` coordinates all `animate()`s in a host (global options, `disabled`, `onComplete`, staggering via per-item `delay`).
- `id`/`inId` enable animating **between different elements** across renders ("magic move").
- Pair with `keyed()` when an element must be treated as new for enter/exit.
- Combining `out:` animations with `inId` magic-moves risks a visible double image (the old element fades while the new one flies). Use a fast, subtle `fadeOut` — or none — on magic-moved elements.

## View Transitions

Free animated navigation/state changes; integrate at the router level (routing.md):

```ts
async #show(url: URL) {
  const apply = () => { this.outlet = match.route.template(data); this.host.requestUpdate(); };
  if (document.startViewTransition) {
    document.startViewTransition(async () => { apply(); await (this.host as LitElement).updateComplete; });
  } else apply();
}
```

Name persistent elements (`view-transition-name: hero`) for morph effects between pages; style with `::view-transition-old/new(...)`. Works with shadow DOM. Cross-document view transitions (`@view-transition { navigation: auto }`) cover SSG/MPA page-to-page transitions with zero JS.

## Observers — @lit-labs/observers (Labs, 2.1.0)

Controller wrappers that connect observer lifecycles to the component lifecycle:

```ts
import { ResizeController } from '@lit-labs/observers/resize-controller.js';

class ChartPanel extends LitElement {
  // Results arrive via the callback option and land on .value
  #size = new ResizeController(this, {
    callback: (entries) => entries.at(-1)?.contentRect.width ?? 300,
  });
  render() { return html`<canvas width=${this.#size.value ?? 300}></canvas>`; }
}
```

Also `IntersectionController` (lazy-load, visibility-gated islands, infinite scroll), `MutationController`, `PerformanceController`. Prefer these over hand-managed observers — disconnection on `hostDisconnected` is handled.

For pure visual response to size, prefer **CSS container queries** over ResizeController; observers are for when JS must react.

## Scroll & misc platform wins

- Scroll-driven animations (`animation-timeline: scroll()/view()`) replace scroll-listener effects — zero JS, compositor-run.
- `content-visibility: auto` on long page sections defers rendering cost without virtualization.
- `<details>`/`popover`/`command`+`commandfor` (where available) before writing accordion/menu JS.
- Drag/gesture logic → a reusable `GestureController` (pointer events + `setPointerCapture`) rather than a library.
