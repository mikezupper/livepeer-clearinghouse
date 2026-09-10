# Modern CSS feature support matrix

**Status as of: 2026-07-22.** Tiers: **A** = Baseline Widely Available (use
unguarded) · **B** = Baseline Newly Available (default choice; guard only if
absence breaks function) · **C** = Limited availability / not all engines
(progressive enhancement behind `@supports` only, with working fallback).

Baseline promotion rule of thumb: Newly → Widely after ~30 months of
interoperability. When updating this file, check <https://web.dev/baseline>,
<https://webstatus.dev>, and MDN.

## Tier A — Baseline Widely Available

| Feature | Syntax reminder | Replaces |
|---|---|---|
| Grid + named areas/lines | `grid-template-areas`, `[full-start]` lines | float/positioning frameworks |
| **Subgrid** (widely since Mar 2026) | `grid-template-rows: subgrid; grid-row: span 3` | JS height equalization |
| Flexbox + `gap` | `display: flex; gap: 1rem` | margin hacks, spacer divs |
| **Container queries (size) + cq units** (widely Feb 2026) | `container-type: inline-size;` `@container (min-width: 400px)`, `cqi/cqb` | ResizeObserver |
| **`:has()`** (widely ~mid-2026) | `form:has(input:invalid)` | JS parent-state classes |
| **Native nesting** (widely ~2026) | `.card { &:hover {} @container {} }` | Sass/Less |
| `@layer` cascade layers | `@layer reset, tokens, components;` | ITCSS/specificity wars |
| `:is()` / `:where()` | `:where(h1,h2,h3)` — zero specificity | selector duplication |
| Logical properties | `inline-size`, `margin-inline`, `inset-block-start` | `[dir=rtl]` duplication |
| `oklch()` / `oklab()` | `oklch(60% 0.19 255)` | hand-tuned hex ramps |
| `color-mix()` | `color-mix(in oklch, var(--brand) 70%, white)` | Sass lighten/darken |
| `clamp()` / `min()` / `max()` | `font-size: clamp(1rem, 0.9rem + 0.5vw, 1.3rem)` | JS fluid type |
| Math/trig fns | `round()`, `mod()`, `pow()`, `sin()`, `cos()` | precomputed values |
| `aspect-ratio` | `aspect-ratio: 16 / 9` | padding-top hack |
| `<dialog>` + `::backdrop` | `dialog.showModal()` | modal libraries, focus traps |
| `display: contents` | remove wrapper from layout | flattening DOM for grid |
| Two-value `display` | `display: inline flex` | ambiguity |
| `overflow: clip` (+ `overflow-clip-margin`) | no scroll container created | unintended scroll containers |
| Scroll snap | `scroll-snap-type: x mandatory; scroll-snap-align: center` | carousel JS core |
| `scroll-behavior`, `scroll-margin/padding` | smooth anchor nav | `scrollTo` animation JS |
| `scrollbar-gutter: stable` | prevent layout shift | width-calc hacks |
| `scrollbar-color` / `scrollbar-width` (newly 2025 → near-widely) | themed scrollbars | ::-webkit hacks |
| `inert` (widely ~Oct 2025) | `<div inert>` | manual tabindex/aria-hidden sweeps |
| `:focus-visible` | keyboard-only focus rings | `:focus` + JS heuristics |
| `:user-valid` / `:user-invalid` | post-interaction validation styling | touched-state JS |
| `accent-color` | theme native controls | custom checkbox rebuilds |
| `content-visibility` (Baseline 2024) + `contain-intrinsic-size` (widely Mar 2026) | `content-visibility: auto; contain-intrinsic-size: auto 500px` | virtualization (some cases) |
| `contain` | `contain: layout paint` | manual perf isolation |
| `lh` / `rlh` units (widely May 2026) | `margin-block: 1rlh` | line-height math |
| `::marker`, `@counter-style` | styled list markers | list-style images/JS |
| `::target-text` | highlight scroll-to-text fragments | JS highlight |
| `:state()` (Baseline 2024) | custom-element states | class toggling |
| `prefers-reduced-motion` / `prefers-color-scheme` / `prefers-contrast` | media features | UA sniffing |
| Individual transforms | `translate`, `rotate`, `scale` properties | transform string composition |
| `conic-gradient()` | pie/rings | canvas/SVG |
| `backdrop-filter` | `blur(8px)` glass | duplicated blurred images |
| `mask-image` / `clip-path` (basic) | vignettes, shapes | PNG cutouts |
| CSS custom properties | `--token` + `var()` | preprocessor variables |
| `zoom` (Baseline 2024, Interop 2026 cleanup) | layout-affecting scale | transform+layout hacks |

### Tier A additions — viewport, device & media adaptation

| Feature | Syntax reminder | Replaces |
|---|---|---|
| **Dynamic viewport units** | `dvh`/`svh`/`lvh` (+ `dvi`/`dvb` logical): `min-block-size: 100dvh` | `window.innerHeight` JS, the iOS `100vh` bug |
| **Safe-area insets** | `padding-block-end: env(safe-area-inset-bottom)` (+ `viewport-fit=cover` meta) | notch/home-bar hacks |
| **Interaction media queries** | `@media (hover: hover) and (pointer: fine)` — never show hover-only affordances on touch | touch-detection JS |
| `@media (resolution)` + `image-set()` | `background-image: image-set("a.avif" type("image/avif"), "a.png" 2x)` | JS retina swapping |
| Wide gamut: `color(display-p3 …)` + `@media (color-gamut: p3)` | richer-than-sRGB accents on capable screens | — |
| `@media (dynamic-range: high)` | HDR-aware effects | — |
| `overscroll-behavior` | `contain` / `none` — stop scroll chaining in drawers/modals | scroll-lock JS |
| `touch-action` | `manipulation` — kills 300ms-delay/double-tap-zoom on controls | fastclick-era JS |
| `user-select`, `caret-color` | selection/caret control | — |
| Print: `@page`, `break-inside: avoid`, `print-color-adjust` | real print styles | print-view pages |

### Tier A additions — typography & text

| Feature | Syntax reminder | Replaces |
|---|---|---|
| **Variable fonts** | `font-variation-settings`, `font-weight: 100 900` ranges, `font-optical-sizing: auto` | multiple font files |
| **`font-palette` + `@font-palette-values`** (Baseline 2022) | recolor COLRv1 color fonts | image logotypes |
| **`font-size-adjust`** (Baseline Jul 2024) | `font-size-adjust: ex-height 0.53` — fallback fonts match x-height, less CLS | FOUT jank |
| Line clamping | `display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: clip` (Baseline); unprefixed `line-clamp` Chromium-only | JS truncation |
| `background-clip: text` | gradient/image-filled headlines (`color: transparent`) | SVG text effects |
| `hyphens: auto` (needs `lang` attr) | justified/narrow-column prose | soft-hyphen insertion |
| `:nth-child(An+B of S)` (Baseline 2023) | `:nth-child(2 of .visible)` | JS index recalculation |
| `:focus-within`, `:placeholder-shown`, `:default`, `:indeterminate` | form-state styling | state classes |
| `::file-selector-button` | style file inputs natively | fake upload buttons |
| `mix-blend-mode` / `background-blend-mode` / `isolation: isolate` | duotone images, knockout text | Photoshopped assets |
| `offset-path` (motion path, widely ~2025) | `offset-path: circle(60%); animation: move …` animating `offset-distance` | JS path animation |
| `filter: drop-shadow()` | contour-following shadows on PNG/SVG/clip-path shapes | box-shadow on transparent images |

## Tier B — Baseline Newly Available

| Feature | Since | Syntax reminder | Replaces |
|---|---|---|---|
| **Anchor positioning** | Baseline 2026 (Firefox 147, Jan 2026) | `anchor-name: --a; position-anchor: --a; position-area: block-end; position-try-fallbacks: flip-block` | Popper/Floating UI |
| **View transitions (same-document)** | Baseline Oct 2025 | `document.startViewTransition()`, `view-transition-name`, `view-transition-class` | FLIP libraries |
| **`@scope`** | Baseline Dec 2025 | `@scope (.card) to (.slot) { … }` | CSS Modules-ish isolation |
| **`@property`** | Baseline Jul 2024 | `@property --x { syntax: "<angle>"; … }` | JS variable tweening |
| **Relative color syntax** | Baseline 2024 | `oklch(from var(--brand) calc(l - .1) c h)` | Sass color fns |
| **`light-dark()`** (+ `color-scheme`) | Baseline May 2024 | `color: light-dark(#333, #eee)` | dual token sets |
| **`contrast-color()`** | Baseline Apr 2026 | `color: contrast-color(var(--bg))` | JS contrast computation |
| **`@starting-style`** | Baseline Aug 2024 | entry states for first render / un-display-none | double-rAF hacks |
| **`transition-behavior: allow-discrete`** | Baseline Aug 2024 | `transition: display .3s allow-discrete` | exit-animation JS |
| **Popover API** | Baseline Jan 2025 | `popover`, `popovertarget` (implicit anchor), `:popover-open` | tooltip/menu managers |
| **`:open`** | Baseline May 2026 | open `<details>`/`<dialog>`/`<select>` | `[open]` attr selectors (still fine) |
| **`::details-content`** | Baseline Sept 2025 | animate disclosure content | accordion JS |
| `<details name>` exclusive accordions | Baseline 2024 | `name="faq"` | JS exclusivity |
| **`field-sizing: content`** | Baseline Jun 2026 | auto-growing textarea/input/select | autosize scripts |
| **`shape()`** | Baseline Feb 2026 | `clip-path: shape(from …, curve to …)` — responsive, calc-friendly | static `path()`/SVG |
| **`text-wrap: balance`** | Baseline 2024 | headings | balance-text libs |
| **`text-wrap: pretty`** | Newly (Firefox 2025) | body text orphan control | — |
| **Container style queries** | Newly 2026 | `@container style(--variant: featured)` (+ range syntax in Chromium) | variant prop classes |
| `text-decoration-skip-ink: all` | Newly May 2026 | descender-aware underlines | — |
| `image-rendering` | Newly May 2026 | `pixelated` for pixel art | canvas scaling |

## Tier C — Limited availability (guard with `@supports`; working fallback mandatory)

| Feature | Engines (Jul 2026) | Syntax reminder | Fallback |
|---|---|---|---|
| **Cross-document view transitions** | Chromium, Safari; Firefox pending | `@view-transition { navigation: auto; }` | instant navigation |
| **Scroll-driven animations** | Chromium, Safari 26; Firefox flagged (~83% support) | `animation-timeline: scroll()/view(); animation-range: entry 0% cover 40%` | content visible, unanimated |
| **Customizable `<select>`** | Chrome 135+, Safari 27β; Firefox prototyping | `appearance: base-select` on `select` and `::picker(select)`; `::checkmark`, `<selectedcontent>` | native select |
| **CSS carousels** | Chromium 135+ | `::scroll-button(inline-end)`, `::scroll-marker`, `::scroll-marker-group` | scroll snap + small JS buttons |
| **`scroll-state()` queries** | Chromium 133+ | `container-type: scroll-state;` `@container scroll-state(stuck: top)` | static styling / IO if essential |
| **`scroll-target-group` + `:target-current`** | Chromium late 2025 | scroll-spy nav highlighting | IO-based scroll-spy if essential |
| **`if()`** | Chrome 137+ | `color: if(style(--dark: 1): white; else: black)` | duplicated rules |
| **`@function`** | Chrome 139+ | `@function --alpha(--c <color>, --a) returns <color> { result: … }` | custom properties |
| **`display: grid-lanes`** (masonry — CSSWG-settled name; old `grid-template-rows: masonry` lost) | experimental, no stable engine | `display: grid-lanes; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))` | grid or `columns` |
| **`interpolate-size` / `calc-size()`** | Chromium 129+ | `:root { interpolate-size: allow-keywords }` → animate to `auto` | grid `0fr→1fr` trick |
| **`corner-shape`** | Chromium 139+ | `corner-shape: squircle` (needs border-radius) | border-radius |
| **`sibling-index()` / `sibling-count()`** | Chrome 138+ | `animation-delay: calc(sibling-index() * 80ms)` | `:nth-child` delays |
| **Typed `attr()`** | Chromium 133+ | `background: attr(data-c type(<color>), gray)`; detect `@supports (x: attr(x type(*)))` | custom properties via style attr |
| **`reading-flow` / `reading-order`** | Chrome 137+ | `reading-flow: flex-visual` | avoid reordering interactive content |
| **`text-box` / `text-box-trim`** | Chromium 133+, Safari 18.2+; no Firefox | `text-box: trim-both cap alphabetic` | line-height tuning |
| **`stretch` sizing keyword** | Chromium 138+ | `height: stretch` | `100%` / flex |
| **`dialog closedby`** | Chrome 134+, Firefox 140+; Safari lagging | `<dialog closedby="any">` | Esc/JS close |
| **Invoker commands** | Chromium 135+, Safari 2026 | `<button commandfor="d" command="show-modal">` | small JS handler |
| **Interest invokers** | Chromium experimental | `interestfor` + `interest-delay` | popover + JS hover |
| **`popover="hint"`** | Chromium | non-stacking ephemeral popovers | `popover="auto"` |
| **Media pseudo-classes** | Interop 2026 area | `:playing`, `:paused`, `:muted`, `:buffering` | JS state classes |
| **Custom highlights / `::search-text`** | Interop 2026 area | style ranges without spans | span wrapping |
| **Anchored container queries** | Chromium | `@container anchored(fallback: flip-block)` | static arrow |
| **Scroll snap events** | Chromium 129+, Safari 18.2+ | `scrollsnapchange`/`scrollsnapchanging` | scroll+IO |
| **Nested view-transition groups** | Chromium | `view-transition-group: nearest` | flat transitions |
| **Scroll-triggered animations** | Chromium experimental | run-once at scroll threshold | scroll-driven / IO |

### Tier C additions

| Feature | Engines (Jul 2026) | Syntax reminder | Fallback |
|---|---|---|---|
| **`anchor-size()`** | ships with anchor positioning (Tier B core) but verify per-engine | `inline-size: anchor-size(inline)` — size popover to its anchor | fixed width |
| **`timeline-scope`** | Chromium | lets an ancestor share a named scroll/view timeline across subtrees | animate within same element |
| **`initial-letter`** | Chromium 110+, Safari (no Firefox) | `p::first-letter { initial-letter: 3; }` drop caps | float + font-size |
| **`margin-trim`** | Safari 16.4+ only | container strips children's outer margins | `> :first-child { margin-block-start: 0 }` |
| **`hanging-punctuation`** | Safari only | `hanging-punctuation: first last` | — |
| **`text-spacing-trim`**, `word-break: auto-phrase` | Chromium | CJK punctuation/phrase-aware breaking | default breaking |
| **`abs()` / `sign()`** | Firefox, Safari; Chromium behind flag | conditional-direction math | `max(x, -1 * x)` trick |
| **`prefers-reduced-transparency`**, `inverted-colors` | partial (Chromium 118+/Safari) | reduce glass effects when asked | opaque default |
| **`view-transition-name: match-element` / `auto`** | Chromium | auto-generated names for lists | explicit per-item names |
| **`:has-slotted`**, `::part` / `::slotted` context | newly 2025 / widely | Shadow DOM styling hooks | — |
| **`object-view-box`** | Chromium | crop/zoom `<img>` like SVG viewBox | wrapper + object-position |

## Watchlist — spec'd or experimental, not yet usable (re-check each update)

- **`random()` / `random-item()`** — CSS Values 5; Chromium experimenting 2026. Randomized design without JS.
- **`:heading()` / `:heading(2)`** — select headings by level; Chromium experimental.
- **Native `@mixin` / `@apply`** — CSSWG-resolved follow-up to `@function`; not shipped anywhere.
- **`device-posture` / foldable viewport segments** — dual-screen adaptation.
- **Scroll-triggered (run-once) animations** — Chromium experimental, distinct from scroll-driven.
- **`sibling-index()`-based grid auto-placement tricks, `item-flow`** — CSSWG active work alongside grid-lanes.
- **CSS gap decorations (`column-rule` in flex/grid, `rule-*`)** — Chromium 139 experimental; styled gutters without borders.

## Known traps

- **`color-contrast()` does not exist in shipping browsers** — some articles
  claim wide support; that's wrong. Use `contrast-color()` (Tier B).
- **Masonry syntax history:** `grid-template-rows: masonry` (old
  Firefox/Safari) and `display: masonry` were both superseded by
  `display: grid-lanes`. Don't emit the dead syntaxes.
- **`:valid`/`:invalid` fire before user interaction** — use
  `:user-valid`/`:user-invalid` for UX.
- The W3C CSS Snapshot ranks by *spec maturity*, not browser adoption —
  `:has()`, nesting, and color-mix sit in "rough interop" tiers there while
  being fully production-safe. Trust Baseline for author decisions; use the
  Snapshot's §4 "safe to ship pre-CR" list as a secondary signal.
- Interop 2026 CSS focus areas (features about to get safer): anchor
  positioning, zoom, container style queries, typed attr(), contrast-color(),
  custom highlights, dialogs/popovers, media pseudo-classes, scroll-driven
  animations, scroll snap, shape(), view transitions.
