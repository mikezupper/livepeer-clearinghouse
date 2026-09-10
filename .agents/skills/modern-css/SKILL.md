---
name: modern-css
description: >
  Build web apps and UIs with modern, platform-native CSS instead of JavaScript
  hacks and heavy libraries. Use whenever creating or modifying web UI: pages,
  components, layouts, themes, animations, dropdowns, tooltips, modals,
  carousels, accordions, forms, or any HTML/CSS/frontend work. Produces
  performant, accessible, elegant interfaces using the full modern CSS platform
  (container queries, :has(), anchor positioning, view transitions,
  scroll-driven animations, oklch color, cascade layers, and more).
metadata:
  last-verified: 2026-07-22
  update-policy: See "Maintaining this skill" at the end of this file.
---

# Modern CSS — build stunning UIs with the platform, not hacks

## Core philosophy

Two decades of frontend "best practice" — jQuery plugins, Popper.js, Masonry.js,
autosize scripts, scroll listeners, ResizeObserver hacks, CSS-in-JS, Sass color
functions, FLIP animation libraries — exists to patch shortcomings CSS no longer
has. Your default is inverted from that legacy:

1. **CSS first, always.** Before writing any JavaScript for layout, positioning,
   theming, animation, state-styling, or scroll behavior, check the
   [JS-hack replacement table](#replace-the-hack) below. If CSS can do it, CSS
   does it. JS is for application logic, data, and things the platform truly
   cannot express.
2. **HTML does more than you think.** `<dialog>`, `popover`, `<details name>`,
   invoker `commandfor`/`command`, `inert` — reach for the semantic, top-layer,
   focus-managed, accessible primitive before building a div-based imitation.
3. **Declarative beats imperative.** Declarative code is smaller, runs off the
   main thread (scroll-driven animations, compositor transitions), can't leak
   listeners, and gets browser accessibility for free.
4. **Progressive enhancement, never broken baselines.** Cutting-edge features
   are used so that unsupported browsers get a *working, good* experience —
   not a broken one. The tier policy below makes this mechanical.
5. **Design like it matters.** Fast and correct is the floor. Aim for
   interfaces with real typographic rhythm, perceptually-even color, purposeful
   motion, and detail (optical alignment, squircle corners, balanced headlines).
   The "Craft" section below is not optional garnish — it is the deliverable.

## Feature tier policy

Every CSS feature you use falls in one of three tiers. The current assignment
of features to tiers lives in [references/feature-support.md](references/feature-support.md)
— consult it when unsure; it is dated and updated as Baseline moves.

- **Tier A — Baseline Widely Available.** Use freely, unguarded, everywhere.
  (Grid, subgrid, flexbox, container queries, `:has()`, nesting, `@layer`,
  `oklch()`, `color-mix()`, logical properties, `clamp()`, `aspect-ratio`,
  `dialog`/`::backdrop`, `display: contents`, `overflow: clip`, `inert`,
  `:focus-visible`, `:user-valid/:user-invalid`, `lh`/`rlh`, trig/math
  functions, scroll snap, `content-visibility`…)
- **Tier B — Baseline Newly Available.** Use as the default; degradation must
  be graceful but needs no guard when absence merely loses polish
  (`@starting-style`, `transition-behavior: allow-discrete`, `@property`,
  `light-dark()`, relative color syntax, `text-wrap: balance/pretty`, popover,
  same-document view transitions, `@scope`, `::details-content`,
  `field-sizing`, `shape()`, `contrast-color()`, anchor positioning,
  container style queries, `:open`…). Guard with `@supports` only when
  absence would *break function or legibility*, not merely lose polish.
- **Tier C — Limited availability / single-engine.** Use only as progressive
  enhancement behind `@supports`, with a fully functional fallback. Never let
  core functionality depend on Tier C. (Cross-document view transitions,
  scroll-driven animations, customizable `<select>`, CSS carousels,
  `scroll-state()` queries, `if()`, `@function`, `display: grid-lanes`
  masonry, `interpolate-size`/`calc-size()`, `corner-shape`,
  `sibling-index()`, typed `attr()`, `reading-flow`, `text-box-trim`…)

Feature-detect with `@supports (property: value)` / `@supports selector(...)`
/ `@supports at-rule(...)` where available — never with user-agent sniffing.

<a name="replace-the-hack"></a>
## Replace the hack: JS/library → modern CSS

When you're about to write any of the left column, write the right column instead.

| The old hack | The modern platform way | Tier |
|---|---|---|
| Popper.js / Floating UI, scroll+resize listeners for tooltips & menus | Anchor positioning: `anchor-name`/`position-anchor`, `position-area`, `anchor()`, `position-try-fallbacks` for auto-flip | B |
| JS modal libraries, focus traps, overlay divs | `<dialog>` + `showModal()` + `::backdrop`; light-dismiss via `closedby="any"` (C) | A |
| Tooltip/dropdown open-close state in JS | `popover` attribute + `popovertarget` (implicit anchor!), `:popover-open`; declarative `command`/`commandfor` invokers (C) | B |
| jQuery-style parent/previous-sibling selection, state classes toggled by JS | `:has()` — form validity, checked-driven layout, "style parent when child X" | A |
| ResizeObserver for component responsiveness | Container queries + `cqi`/`cqb` units; `@container style(...)` for variant/theme propagation | A/B |
| Media-query-driven component styling | Container queries (components respond to their container, not viewport) | A |
| Masonry.js / column hacks | `display: grid-lanes` (@supports-guarded) falling back to grid/columns | C |
| Equal-height card internals via JS measurement | Subgrid: `grid-template-rows: subgrid; grid-row: span 3` | A |
| Textarea autosize scripts | `field-sizing: content` | B |
| "Animate height: auto" scrollHeight hacks | `interpolate-size: allow-keywords` (+ `calc-size()`), fallback to grid `grid-template-rows: 0fr → 1fr` trick | C |
| Entry-animation double-rAF / class-toggle hacks | `@starting-style` + `transition-behavior: allow-discrete` (animate from/to `display: none`) | B |
| FLIP libraries, route-transition animation code | View transitions: `document.startViewTransition()`, `view-transition-name`; cross-document `@view-transition` (C) | B |
| Scroll listeners for progress bars, reveals, parallax | Scroll-driven animations: `animation-timeline: scroll()` / `view()` + `animation-range` — off main thread | C |
| IntersectionObserver for "is sticky stuck" / scroll-spy | `@container scroll-state(stuck: top / snapped / scrollable)` (C); `scroll-target-group` + `:target-current` (C) |
| Carousel libraries | Scroll snap (A) + `::scroll-button()`/`::scroll-marker` (C) as enhancement |
| Accordion JS | `<details name="group">` exclusive accordions + `::details-content` transition | A/B |
| Custom dropdown/select libraries | Customizable `<select>`: `appearance: base-select` + `::picker(select)` behind `@supports`, native select fallback | C |
| Sass `darken()`/`lighten()`, JS theme palette generation | `oklch()` + relative color syntax `oklch(from var(--brand) calc(l - .1) c h)` + `color-mix(in oklch, …)` | A/B |
| Duplicate variable sets for dark mode | `color-scheme: light dark` + `light-dark()` | B |
| JS computing readable text color on dynamic backgrounds | `contrast-color()` | B |
| Sass nesting, BEM discipline, CSS-in-JS scoping | Native nesting (A), `@layer` (A), `@scope` with donut boundaries (B) |
| Sass mixins/functions | `@property` typed tokens (B); `@function` and `if()` behind @supports (C); `:root` custom-property systems |
| balance-text libraries, `<br>` in headings | `text-wrap: balance` (headings), `text-wrap: pretty` (body) | B |
| JS-tweened gradients/counters | `@property` registered custom properties → transition/animate the variable itself | B |
| `[dir=rtl]` duplicated rule sets | Logical properties (`inline-size`, `margin-inline`, `padding-block`, `inset-inline-start`) | A |
| Stagger delays hard-coded per `:nth-child` | `sibling-index()` / `sibling-count()` (C) with `:nth-child` fallback |
| SVG clip hacks for fancy shapes | `clip-path: shape(...)` — responsive, calc()-friendly (B); `corner-shape: squircle` (C) |
| Lazy-render virtualization (when scroll pos need not be exact) | `content-visibility: auto` + `contain-intrinsic-size: auto <est>` | A |
| Media-player state classes | `:playing`, `:paused`, `:muted` pseudo-classes (C) |
| Data → style bridges via inline styles/CSS-in-JS | Typed `attr(data-x type(<number>))` (C); custom properties set on elements (A) |

## Architecture: how every stylesheet starts

Structure every project's CSS the same way — layered, token-driven, logical:

```css
/* 1. Declare layer order up front — later layers always win. */
@layer reset, tokens, base, layout, components, utilities;

/* 2. Reset (small, modern). */
@layer reset {
  *, *::before, *::after { box-sizing: border-box; }
  * { margin: 0; }
  img, picture, video, canvas, svg { display: block; max-inline-size: 100%; }
  input, button, textarea, select { font: inherit; }
  p, h1, h2, h3, h4 { overflow-wrap: break-word; }
}

/* 3. Design tokens: typed, perceptual, theme-aware. */
@layer tokens {
  :root {
    color-scheme: light dark;

    /* Perceptual color system — one brand hue, everything derived. */
    --hue: 255;
    --brand: oklch(60% 0.19 var(--hue));
    --brand-hover: oklch(from var(--brand) calc(l - 0.07) c h);
    --surface: light-dark(oklch(99% 0.005 var(--hue)), oklch(18% 0.015 var(--hue)));
    --surface-raised: light-dark(oklch(100% 0 0), oklch(23% 0.02 var(--hue)));
    --text: light-dark(oklch(22% 0.02 var(--hue)), oklch(93% 0.01 var(--hue)));
    --text-muted: color-mix(in oklch, var(--text) 62%, var(--surface));
    --border: color-mix(in oklch, var(--text) 14%, var(--surface));

    /* Fluid space & type scales. */
    --space: clamp(1rem, 0.5rem + 1.5vw, 1.5rem);
    --step-0: clamp(1rem, 0.95rem + 0.25vw, 1.125rem);
    --step-1: clamp(1.25rem, 1.1rem + 0.7vw, 1.62rem);
    --step-2: clamp(1.56rem, 1.3rem + 1.3vw, 2.33rem);

    --radius: 12px;
    --shadow: 0 1px 2px oklch(20% 0.02 var(--hue) / 0.08),
              0 8px 24px oklch(20% 0.02 var(--hue) / 0.10);
  }

  /* Register tokens you intend to animate. */
  @property --hue { syntax: "<number>"; inherits: true; initial-value: 255; }
}

@layer base {
  body {
    background: var(--surface);
    color: var(--text);
    font-size: var(--step-0);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  h1, h2, h3 { text-wrap: balance; line-height: 1.15; }
  p, li, figcaption { text-wrap: pretty; max-inline-size: 65ch; }
  :focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
}
```

Rules that follow from this architecture:

- **Always `@layer`.** Un-layered styles beat all layers — reserve that for rare
  overrides. Third-party CSS goes in a low layer: `@import url(x.css) layer(vendor);`
- **Nesting for locality, `@scope` for isolation.** Nest component styles
  (`&:hover`, nested `@media`/`@container`). Use `@scope (.card) to (.slot)`
  when you need donut boundaries instead of Shadow DOM or naming conventions.
- **Logical properties only.** `inline-size` not `width`-thinking,
  `margin-inline`, `padding-block`, `inset-block-start`, `border-start-start-radius`.
  Physical properties only when the effect is genuinely physical (e.g. shadows).
- **All color in `oklch`.** Derive every tint, shade, hover, and border via
  relative color syntax or `color-mix(in oklch, …)` from few base tokens.
  Never hand-pick 40 hex codes. Use `contrast-color()` for text over dynamic
  backgrounds, with a manual `light-dark()` fallback.
- **Both themes always.** `color-scheme: light dark` + `light-dark()` from the
  first commit — never bolt dark mode on later. Respect
  `prefers-contrast` and `forced-colors` for critical UI.

## Layout playbook

Pick the tool by intent, not habit:

- **Page scaffold:** Grid with named areas or named lines; the *breakout/full-bleed*
  wrapper pattern (`grid-template-columns: [full-start] minmax(var(--space), 1fr)
  [content-start] min(100% - 2*var(--space), 72rem) [content-end]
  minmax(var(--space), 1fr) [full-end]`) for prose with edge-to-edge sections.
- **1-D flow (toolbars, nav, tag lists, split nav):** Flexbox + `gap`; push
  groups apart with `margin-inline-start: auto`, never spacer divs.
- **2-D alignment (cards, forms, dashboards):** Grid. Card grids:
  `grid-template-columns: repeat(auto-fit, minmax(min(18rem, 100%), 1fr))`.
- **Aligned internals across siblings:** Subgrid (`grid-row: span 3;
  grid-template-rows: subgrid`) — never JS height equalization.
- **Components:** wrap in a container (`container-type: inline-size`) and use
  `@container` + `cqi` units. Media queries are for page-level layout only.
- **Waterfall galleries:** `@supports (display: grid-lanes)` enhancement over a
  responsive grid or `columns` fallback.
- **Centering:** flex/grid `place-content: center`; `margin-inline: auto` for
  blocks. `aspect-ratio` for media boxes; `object-fit: cover` for images.
- **Sticky chrome:** `position: sticky` + `scroll-margin-top` on targets;
  detect stuck-state with `scroll-state()` where supported.
- Prefer `overflow: clip` over `hidden` when you don't need a scroll container.
  Reserve scrollbar space with `scrollbar-gutter: stable`.
- **Viewport reality:** full-height sections use `100dvh` (or `svh` for
  stable chrome), never bare `100vh`; respect notches/home bars with
  `env(safe-area-inset-*)` + `viewport-fit=cover`; contain scroll chaining in
  drawers/sheets with `overscroll-behavior: contain`.
- **Input reality:** hover-revealed affordances only inside
  `@media (hover: hover) and (pointer: fine)`; touch users get
  always-visible controls. `touch-action: manipulation` on tappable controls.
- If you visually reorder (`order`, dense grid flow), fix accessibility with
  `reading-flow` where supported and avoid reordering interactive content otherwise.

## Interaction & motion playbook

Motion must be purposeful (orient, connect, confirm), fast (150–350ms UI,
`ease-out` entering, `ease-in` leaving), and interruptible.

- **Enter/exit:** `@starting-style` + `transition-behavior: allow-discrete` on
  `display`/`overlay`. This is the standard pattern for dialogs, popovers,
  toasts, menus — no JS timing.
- **Shared-element / route transitions:** same-document view transitions with
  `view-transition-name` (use `view-transition-class` to style groups; names
  must be unique per snapshot). For MPAs add `@view-transition { navigation: auto; }`
  as Tier C enhancement.
- **Scroll effects:** `animation-timeline: view()` + `animation-range: entry 0%
  cover 40%` for reveals; `scroll()` for progress bars — always inside
  `@supports (animation-timeline: scroll())`, and design so content is fine
  without it (no content hidden behind un-run animations in fallback browsers!).
- **Micro-interactions:** transition `transform`/`opacity` (compositor-friendly)
  — never `top/left/width/height`. Springy feel via `linear()` easing curves.
  Animate registered `@property` tokens for gradient/hue/number effects.
- **Stagger:** `sibling-index()` behind `@supports`, else a few `:nth-child` delays.
- **Always:**

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

(Or better: gate non-essential motion behind `@media (prefers-reduced-motion:
no-preference)` so reduced is the default.)

## Component playbook

- **Modal:** `<dialog>` + `.showModal()`. Style `::backdrop`
  (`backdrop-filter: blur(8px)`), animate with `@starting-style`, add
  `closedby="any"` for light dismiss. Never rebuild focus trapping.
- **Tooltip / hovercard / menu:** `popover` (+ `popover="hint"` where supported)
  positioned via anchor positioning; `popovertarget` gives an implicit anchor —
  often zero custom anchor names needed. Provide `position-try-fallbacks:
  flip-block, flip-inline` so it never overflows.
- **Accordion:** `<details name="faq">` for exclusivity; animate
  `::details-content` (+ `interpolate-size: allow-keywords` enhancement).
- **Custom select:** `@supports (appearance: base-select)` → style
  `select`, `::picker(select)`, `::checkmark`, `<selectedcontent>`; everyone
  else gets the trusty native select. Never a div-listbox.
- **Carousel:** scroll snap (`scroll-snap-type: x mandatory` +
  `scroll-snap-align`) as the working base; `::scroll-button()` /
  `::scroll-marker` as Tier C enhancement; else minimal JS buttons calling
  `scrollIntoView`.
- **Forms:** `:user-valid` / `:user-invalid` (never bare `:valid/:invalid` —
  they fire before interaction), `:has()` to style the field wrapper and to
  gate submit buttons, `field-sizing: content` on textareas,
  `accent-color` for cheap native-control theming.
- **Toasts/notifications:** popover + `@starting-style`; stack in a grid.
- **Empty/loading states:** design them; skeletons via animated
  `color-mix` gradients, not layout-shifting spinners.

## Performance playbook

- **Rendering:** `content-visibility: auto` + `contain-intrinsic-size: auto 500px`
  on long below-the-fold sections (feeds, comment lists, doc pages).
  `contain: layout paint` on isolated widgets.
- **Animation:** compositor properties only (`transform`, `opacity`,
  registered custom properties where cheap); `will-change` sparingly and
  removed after use. Scroll-driven animations run off main-thread — prefer
  them over scroll listeners even ignoring ergonomics.
- **CLS:** always reserve space — `aspect-ratio` on media, `scrollbar-gutter:
  stable`, `font-display: swap` + metric-compatible fallbacks (`size-adjust`,
  `ascent-override`).
- **Loading:** one small CSS file per route where possible; `@layer`-ed
  imports; no runtime CSS-in-JS. System font stack or one variable font.
- **Vertical rhythm cheaply:** `lh`/`rlh` units for margins that match line
  height.

## Accessibility (non-negotiable)

- Semantic HTML first; the platform components above are chosen *because* they
  carry focus and ARIA behavior for free.
- Visible focus: style `:focus-visible`, never remove outlines without replacement.
- Contrast: 4.5:1 body text minimum; verify derived oklch pairs; use
  `contrast-color()` for dynamic backgrounds.
- `inert` on background content when custom overlays exist (dialog does this
  for you).
- Honor `prefers-reduced-motion`, `prefers-color-scheme`, `prefers-contrast`.
- Hit targets ≥ 44×44px (`min-block-size`/`min-inline-size`, padding).
- Don't break reading/tab order with visual reordering (see layout playbook).

## Craft: what makes it stunning, not just correct

- **Type is the design.** A modular fluid scale (use `clamp()`), `text-wrap:
  balance` on headings, `pretty` on prose, 45–75ch measure, real hierarchy from
  size+weight+color-muting — not boxes and borders. `text-box: trim-both cap
  alphabetic` (Tier C) for optically-perfect vertical centering in
  buttons/badges. One **variable font** (weight/optical-size axes) instead of
  many files; `font-size-adjust` so fallback fonts don't jank; gradient
  headlines via `background-clip: text`; drop caps via `initial-letter`
  (Tier C).
- **Color with opinion.** One hue, oklch-derived neutrals *tinted with that
  hue* (never pure gray), restrained accent use. Gradients in `in oklch`
  interpolation to avoid muddy midpoints. Subtle color-mix borders instead of
  `#ddd`.
- **Depth, quietly.** Layered low-opacity shadows (2 layers: contact + ambient),
  `backdrop-filter` glass sparingly, raised-surface tokens — not drop-shadow
  soup.
- **Space generously and systematically.** Everything on the space scale;
  `gap` over margins inside layouts; whitespace is the cheapest luxury.
- **Details.** `::selection` styled to brand, `accent-color`, themed scrollbars
  (`scrollbar-color`), `scroll-behavior: smooth` (motion-gated), squircle
  corners where supported, `::marker` styling, custom `@counter-style` where
  lists matter.
- **Motion as narrative.** View transitions connect states; entry animations
  orient; hover states confirm. If a motion doesn't explain something, cut it.

## Anti-patterns — never do these

- `!important` to win specificity fights (use `@layer`), except inside the
  reduced-motion kill-switch.
- Pixel media queries for component behavior (use container queries).
- Div-based fake dialogs/selects/checkboxes; `onclick` divs instead of buttons.
- JS measuring layout to set styles (`el.offsetHeight`-driven CSS).
- Hex/rgb hand-tuned palettes; duplicated dark-theme variable sets.
- `overflow: hidden` reflexively where `clip` is meant.
- Bootstrap/heavy UI kits or runtime CSS-in-JS for greenfield work — the
  platform plus this skill's architecture is lighter and better.
- Assuming a Tier C feature works everywhere because it worked in your test
  browser. Check [references/feature-support.md](references/feature-support.md).

## Workflow

1. Sketch the page as regions → semantic HTML with platform components.
2. Lay down the architecture skeleton (layers, tokens, reset, base) from above.
3. Build layout mobile-first with grid/flex + container queries.
4. Add components per the playbook; check the hack-replacement table before
   any JS.
5. Add motion (entry/exit, view transitions, scroll enhancements), all
   reduced-motion-gated.
6. Audit: keyboard-only pass, both themes, 200% zoom, narrow container,
   `prefers-reduced-motion`, no console errors, no layout shift.
7. For anything cutting-edge, confirm tier in
   [references/feature-support.md](references/feature-support.md) and guard
   accordingly.

Detailed copy-paste patterns: [references/recipes.md](references/recipes.md).

## Maintaining this skill

CSS moves fast; this skill must move with it. **Re-verify roughly every 6
months** (last verified: see frontmatter):

1. Check current Baseline status: <https://web.dev/baseline> (monthly digests),
   <https://webstatus.dev>, MDN per-feature pages
   (<https://developer.mozilla.org/en-US/docs/Web/CSS/Reference>).
2. Check the year's Interop focus areas (<https://wpt.fyi/interop>) — these
   predict what's about to become safe.
3. Check the latest W3C CSS Snapshot (<https://www.w3.org/TR/CSS/>) §4
   "safe to release pre-CR" list.
4. Promote features between tiers in `references/feature-support.md`
   (C→B when all engines ship; B→A at Baseline widely available), add new
   features with syntax + the hack they replace, and update the frontmatter
   `last-verified` date.
5. Scan Chrome "CSS Wrapped", the WebKit and Firefox release notes for
   features this skill doesn't mention yet, and re-check the Watchlist section
   of `references/feature-support.md`.

Full source list, update procedure, and tier-promotion rules:
[README.md](README.md#keeping-the-skill-updated).
