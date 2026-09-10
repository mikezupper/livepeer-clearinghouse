# Modern CSS recipes

Copy-paste patterns implementing the playbooks in SKILL.md. Every recipe is
production-shaped: tiered, accessible, reduced-motion-safe.

## 1. Page scaffold with breakout sections

```css
.page {
  display: grid;
  grid-template-columns:
    [full-start] minmax(var(--space), 1fr)
    [content-start] min(100% - 2 * var(--space), 72rem) [content-end]
    minmax(var(--space), 1fr) [full-end];
}
.page > * { grid-column: content; }
.page > .full-bleed { grid-column: full; }
```

## 2. Responsive card grid + subgrid-aligned internals

```css
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(18rem, 100%), 1fr));
  gap: var(--space);
}
.card {
  display: grid;
  grid-row: span 3;                 /* header / body / footer */
  grid-template-rows: subgrid;
  gap: 0.75rem;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space);
  box-shadow: var(--shadow);
}
```

## 3. Container-query component (self-responsive card)

```css
.card-wrapper { container: card / inline-size; }

.media-card { display: grid; gap: 1rem; }

@container card (min-width: 420px) {
  .media-card { grid-template-columns: 40cqi 1fr; align-items: center; }
}
.media-card h3 { font-size: clamp(1.1rem, 4cqi, 1.5rem); }
```

Variant propagation with style queries (Tier B):

```css
.card-wrapper { --variant: default; }
.card-wrapper.featured { --variant: featured; }

@container style(--variant: featured) {
  .media-card { border-color: var(--brand); background:
    color-mix(in oklch, var(--brand) 6%, var(--surface-raised)); }
}
```

## 4. Complete theme system (one hue in, whole palette out)

```css
:root {
  color-scheme: light dark;
  --hue: 255;
  --brand: oklch(60% 0.19 var(--hue));

  --surface:        light-dark(oklch(99% 0.005 var(--hue)), oklch(18% 0.015 var(--hue)));
  --surface-raised: light-dark(oklch(100% 0 0),             oklch(23% 0.02 var(--hue)));
  --text:           light-dark(oklch(22% 0.02 var(--hue)),  oklch(93% 0.01 var(--hue)));
  --text-muted:     color-mix(in oklch, var(--text) 62%, var(--surface));
  --border:         color-mix(in oklch, var(--text) 14%, var(--surface));

  --brand-hover:  oklch(from var(--brand) calc(l - 0.07) c h);
  --brand-subtle: color-mix(in oklch, var(--brand) 10%, var(--surface));
  --on-brand:     contrast-color(var(--brand));   /* Tier B */
}

/* Manual theme override (e.g. a toggle) — keeps light-dark() working: */
[data-theme="dark"]  { color-scheme: dark; }
[data-theme="light"] { color-scheme: light; }
```

## 5. Dialog with entry/exit animation, zero JS beyond show/close

```html
<button commandfor="confirm" command="show-modal">Delete…</button>
<dialog id="confirm" closedby="any">…</dialog>
<!-- Fallback for non-invoker browsers: btn.onclick = () => dialog.showModal() -->
```

```css
dialog {
  border: none; border-radius: var(--radius);
  box-shadow: var(--shadow); padding: var(--space);
  opacity: 1; scale: 1;
  transition: opacity .25s ease-out, scale .25s ease-out,
              display .25s allow-discrete, overlay .25s allow-discrete;
}
dialog::backdrop {
  background: oklch(15% 0.02 var(--hue) / 0.4);
  backdrop-filter: blur(6px);
  transition: opacity .25s, display .25s allow-discrete, overlay .25s allow-discrete;
}
/* Exit state */
dialog:not([open]) { opacity: 0; scale: 0.96; }
/* Entry state */
@starting-style {
  dialog[open] { opacity: 0; scale: 0.96; }
  dialog[open]::backdrop { opacity: 0; }
}
```

## 6. Tooltip / dropdown: popover + anchor positioning

```html
<button popovertarget="tip">Info</button>
<div id="tip" popover>Anchored automatically to its invoker.</div>
```

```css
[popover] {
  /* popovertarget creates an implicit anchor — no anchor-name needed */
  position: absolute;
  position-area: block-end center;
  position-try-fallbacks: flip-block, flip-inline;
  margin-block-start: 0.5rem;
  inset: auto;                 /* clear UA centering */
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-raised);
  box-shadow: var(--shadow);
  padding: 0.5rem 0.75rem;

  transition: opacity .18s ease-out, translate .18s ease-out,
              display .18s allow-discrete, overlay .18s allow-discrete;
  opacity: 0; translate: 0 4px;
}
[popover]:popover-open { opacity: 1; translate: 0 0; }
@starting-style {
  [popover]:popover-open { opacity: 0; translate: 0 4px; }
}
```

Explicit anchors (when trigger ≠ invoker):

```css
.trigger { anchor-name: --menu; }
.menu    { position-anchor: --menu; position-area: block-end span-inline-end; }
```

## 7. Animated exclusive accordion, no JS

```html
<details name="faq"><summary>Question one</summary><p>…</p></details>
<details name="faq"><summary>Question two</summary><p>…</p></details>
```

```css
:root { interpolate-size: allow-keywords; }   /* Tier C opt-in, harmless elsewhere */

details::details-content {
  opacity: 0;
  block-size: 0;
  overflow-y: clip;
  transition: content-visibility .3s allow-discrete,
              opacity .3s, block-size .3s;
}
details[open]::details-content { opacity: 1; block-size: auto; }

summary { cursor: pointer; }
summary::marker { color: var(--brand); }
```

## 8. Scroll-reveal + reading progress (pure enhancement)

```css
@supports (animation-timeline: view()) {
  @media (prefers-reduced-motion: no-preference) {
    .reveal {
      animation: fade-up both;
      animation-timeline: view();
      animation-range: entry 0% cover 35%;
    }
  }
}
@keyframes fade-up { from { opacity: 0; translate: 0 24px; } }

/* Reading progress bar */
@supports (animation-timeline: scroll()) {
  .progress {
    position: fixed; inset-block-start: 0; inset-inline: 0;
    block-size: 3px; background: var(--brand);
    transform-origin: 0 50%;
    animation: grow linear both;
    animation-timeline: scroll(root);
  }
  @keyframes grow { from { scale: 0 1; } to { scale: 1 1; } }
}
```

Content must be fully visible when the `@supports` block doesn't apply —
never set `opacity: 0` outside the guard.

## 9. Carousel: snap base + CSS-generated controls enhancement

```css
.carousel {
  display: grid; grid-auto-flow: column; grid-auto-columns: 80%;
  gap: var(--space);
  overflow-x: auto; overscroll-behavior-x: contain;
  scroll-snap-type: x mandatory; scroll-padding-inline: var(--space);
  scrollbar-width: none;
}
.carousel > * { scroll-snap-align: center; }

/* Tier C: browser-generated, accessible buttons + dots */
@supports selector(::scroll-button(*)) {
  .carousel { anchor-name: --carousel; }
  .carousel::scroll-button(inline-start) { content: "‹" / "Previous"; }
  .carousel::scroll-button(inline-end)   { content: "›" / "Next"; }
  .carousel { scroll-marker-group: after; }
  .carousel > *::scroll-marker { content: ""; /* dot styles */ }
  .carousel > *::scroll-marker:target-current { background: var(--brand); }
}
```

## 10. Form UX entirely in CSS

```css
input, textarea, select {
  border: 1px solid var(--border); border-radius: calc(var(--radius) / 2);
  padding: 0.5rem 0.75rem; background: var(--surface-raised);
  transition: border-color .15s, outline-color .15s;
}
textarea { field-sizing: content; min-block-size: 3lh; max-block-size: 12lh; }

input:user-invalid { border-color: oklch(55% 0.19 25); }
input:user-valid   { border-color: oklch(55% 0.12 150); }

.field:has(:user-invalid) .error { display: block; }
.error { display: none; color: oklch(55% 0.19 25); font-size: 0.875em; }

/* Gate submit until required boxes are ticked */
form:has(.terms:not(:checked)) [type="submit"] {
  opacity: 0.5; pointer-events: none;
}

::placeholder { color: var(--text-muted); }
:is(input, textarea, select, button):focus-visible {
  outline: 2px solid var(--brand); outline-offset: 1px;
}
```

## 11. Customizable select (Tier C, native fallback built in)

```css
@supports (appearance: base-select) {
  select, select::picker(select) { appearance: base-select; }
  select {
    border-radius: var(--radius); padding: 0.5rem 0.75rem;
    background: var(--surface-raised);
  }
  select::picker(select) {
    border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: var(--shadow); padding: 0.25rem;
    transition: opacity .15s, display .15s allow-discrete, overlay .15s allow-discrete;
    opacity: 0;
  }
  select:open::picker(select) { opacity: 1; }
  @starting-style { select:open::picker(select) { opacity: 0; } }
  option { border-radius: calc(var(--radius) / 2); padding: 0.4rem 0.6rem; }
  option:checked { background: var(--brand-subtle); }
  option::checkmark { color: var(--brand); }
}
```

## 12. View transitions

Same-document (Tier B):

```css
.thumbnail { view-transition-name: var(--vt-name); } /* unique per element */
::view-transition-group(*) { animation-duration: 280ms; }
@media (prefers-reduced-motion: reduce) {
  ::view-transition-group(*),
  ::view-transition-old(*),
  ::view-transition-new(*) { animation: none !important; }
}
```

```js
// The only JS: wrap the DOM change.
const update = () => renderNewState();
document.startViewTransition ? document.startViewTransition(update) : update();
```

Cross-document MPA (Tier C — pure enhancement, zero fallback cost):

```css
@view-transition { navigation: auto; }
header { view-transition-name: site-header; }  /* persists across pages */
```

## 13. Sticky header that knows it's stuck (Tier C, graceful)

```css
.header-wrap { container-type: scroll-state; position: sticky; inset-block-start: 0; }

@supports (container-type: scroll-state) {
  @container scroll-state(stuck: top) {
    .header {
      box-shadow: var(--shadow);
      background: color-mix(in oklch, var(--surface) 85%, transparent);
      backdrop-filter: blur(10px);
    }
  }
}
```

## 14. Staggered list entrance

```css
@media (prefers-reduced-motion: no-preference) {
  .list > * { animation: fade-up .4s ease-out backwards; }

  /* Tier C stagger, auto-adapts to item count */
  @supports (animation-delay: calc(sibling-index() * 1ms)) {
    .list > * { animation-delay: calc(sibling-index() * 60ms); }
  }
  /* Fallback stagger */
  @supports not (animation-delay: calc(sibling-index() * 1ms)) {
    .list > :nth-child(2) { animation-delay: 60ms; }
    .list > :nth-child(3) { animation-delay: 120ms; }
    .list > :nth-child(n+4) { animation-delay: 180ms; }
  }
}
```

## 15. Long-page rendering performance

```css
.section-below-fold {
  content-visibility: auto;
  contain-intrinsic-size: auto 640px;   /* estimate; prevents scrollbar jumps */
}
```

## 16. Masonry gallery (future-proof)

```css
.gallery { columns: 3 240px; gap: var(--space); }     /* working fallback */
.gallery > * { break-inside: avoid; margin-block-end: var(--space); }

@supports (display: grid-lanes) {
  .gallery {
    display: grid-lanes;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: var(--space);
  }
  .gallery > * { margin-block-end: 0; }
}
```

## 17. Skeleton loading (no layout shift)

```css
.skeleton {
  --shine: color-mix(in oklch, var(--text) 6%, var(--surface));
  background: linear-gradient(100deg in oklch,
    var(--shine) 40%, color-mix(in oklch, var(--shine) 50%, var(--surface)) 50%,
    var(--shine) 60%) 0 0 / 200% 100%;
  border-radius: calc(var(--radius) / 2);
}
@media (prefers-reduced-motion: no-preference) {
  .skeleton { animation: shimmer 1.4s linear infinite; }
}
@keyframes shimmer { to { background-position: -200% 0; } }
```

## 18. Animated gradient via @property (typed token animation)

```css
@property --angle { syntax: "<angle>"; inherits: false; initial-value: 0deg; }

.glow {
  background: conic-gradient(from var(--angle) in oklch,
    var(--brand), oklch(from var(--brand) l c calc(h + 120)),
    oklch(from var(--brand) l c calc(h + 240)), var(--brand));
}
@media (prefers-reduced-motion: no-preference) {
  .glow { animation: spin 6s linear infinite; }
}
@keyframes spin { to { --angle: 360deg; } }
```

## 19. Optical polish details

```css
::selection { background: var(--brand); color: contrast-color(var(--brand)); }

:root { accent-color: var(--brand); scrollbar-color: var(--border) transparent; }

@media (prefers-reduced-motion: no-preference) {
  html { scroll-behavior: smooth; }
}

/* Optically centered button text (Tier C, harmless fallback) */
.button { text-box: trim-both cap alphabetic; }

/* Squircle cards where supported */
@supports (corner-shape: squircle) {
  .card { corner-shape: squircle; border-radius: calc(var(--radius) * 1.6); }
}

/* Descender-aware underlines */
a { text-decoration-thickness: 0.08em; text-underline-offset: 0.15em;
    text-decoration-skip-ink: all; }
```

## 20. `if()` / `@function` (Tier C — future-facing, always guarded)

```css
@supports (color: if(style(--x: 1): red; else: blue)) {
  .card {
    transition-duration: if(media(prefers-reduced-motion: reduce): 0ms; else: 200ms);
  }
}

@supports at-rule(@function) {
  @function --alpha(--c <color>, --a <number>: 0.8) returns <color> {
    result: oklch(from var(--c) l c h / var(--a));
  }
  .overlay { background: --alpha(var(--brand), 0.15); }
}
```
