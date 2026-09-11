# Frontend engineering

## Applications and route ownership

`admin-web` and `user-web` are independent Vite applications. They share
Effect contracts, platform services, the Lit `och-app-shell`, and global design
tokens, but they remain separately built, tested, deployed, and covered. The
browser displays and invokes authoritative API state; financial,
authorization, and policy decisions stay in the Python service.

| Application | Production mount | Information architecture |
| --- | --- | --- |
| `admin-web` | `/admin/` | Overview; Tenants; Payer accounts; Principals; Financials; Policies & pricing; Usage & charges; Operations; Audit trail |
| `user-web` | `/` | Overview; Credentials; Sessions; Catalog; Usage; Charges; Profile |

Admin navigation is grouped as Clearinghouse, Access, Economics, Metering, and
Governance. Operators can see system-wide controls and data; tenant
administrators receive the same task-oriented structure with server-enforced
scope. The canonical route model is
`frontend/apps/admin-web/src/routes.ts`. It preserves the `/admin` mount used by
the edge and also supports a direct-root mount for the standalone development
server. Unknown or incomplete paths fail to Overview.

The user route model is `frontend/apps/user-web/src/navigation.ts`. Its routes
are rooted at `/`; unknown paths fail to Overview. Both applications use the
History API for same-origin client navigation and listen for `popstate`.
Modified clicks retain native browser behavior. Caddy serves each application's
`index.html` fallback, so reloading a valid client route does not produce a web
server 404.

## Shared console shell

`frontend/packages/ui/src/och-app-shell.ts` provides one semantic shell for
both applications. On viewports at least 48rem wide it renders a persistent
16rem sidebar next to a workspace. The workspace contains a compact top bar,
the single `main` landmark, and a footer. Below 48rem the sidebar becomes an
off-canvas navigation panel with explicit open and close controls and a dismiss
backdrop.

The desktop sidebar carries a two-line wordmark: “Livepeer Clearinghouse” with
the Livepeer name in emerald, followed by the muted “Identity & Payments”
purpose line. The compact top bar repeats the product name on mobile while the
sidebar is closed. These are text, not image assets, so they remain crisp,
selectable, and legible in forced colors.

The mobile interaction is part of the accessibility contract:

- the toggle names and controls the navigation with `aria-controls` and
  `aria-expanded`;
- opening moves focus to the visible sidebar close control, keeping dismissal
  available without scrolling even when the navigation is long;
- selecting navigation or using the backdrop closes it;
- Escape closes it and restores focus to the opening control;
- the hidden sidebar uses `visibility: hidden`, preventing off-screen links
  from remaining in keyboard order; and
- while the panel is open, the workspace is `inert`, preventing focus or
  interaction behind the modal layer.

All shell controls have a 2.75rem minimum block size. Navigation links use
`aria-current="page"`; the visible label, shape, and position communicate state
in addition to color.

### Slots

| Slot | Contract |
| --- | --- |
| default | Current route content inside `main` |
| `navigation` | Primary grouped navigation; use lists and links |
| `context` | Compact account, tenant, or role context in the top bar |
| `utility` | Session and utility actions in the top bar |

### Shadow Parts

The shell publishes these stable customization points. An application nesting
the shell in its own Shadow DOM must forward every part it expects global CSS
to reach with `exportparts`.

| Region | Parts |
| --- | --- |
| Access | `skip-link`, `menu-button`, `menu-icon`, `menu-label`, `sidebar-close-button`, `sidebar-close-icon`, `sidebar-close-label`, `navigation-backdrop` |
| Shell | `shell`, `sidebar`, `workspace`, `topbar` |
| Sidebar | `header`, `sidebar-header`, `brand`, `brand-name`, `brand-accent`, `brand-subtitle`, `navigation` |
| Top bar | `mobile-brand`, `context`, `utility` |
| Content | `main`, `heading-group`, `title`, `summary`, `content` |
| Footer | `footer`, `footer-note` |

`header` is retained as the backward-compatible name for the sidebar header.
The generated package contract is
`frontend/packages/ui/custom-elements.json`; change the JSDoc slot/part
annotations and regenerate the manifest whenever this public surface changes.

## Visual language and tokens

The visual system borrows the useful character of Pymthouse's operator UI: a
compact, data-dense console, near-neutral zinc surfaces, an emerald primary
signal, restrained teal support, and quiet depth. The default is deliberately
dark: zinc-950 canvas and sidebar, zinc-900 raised surfaces, zinc-800 quiet
dividers, zinc-100 primary text, zinc-400 secondary text, and emerald-400 brand
and focus treatment. It does not copy Pymthouse's code, Tailwind utility
classes, product identity, dark-only limitation, or commercial workflows.
Pymthouse is design research, not a frontend dependency or executable
specification.

`frontend/packages/styles/global.css` is the canonical token source. Tokens
use the `--och-` namespace and are grouped by responsibility:

| Vocabulary | Tokens and intent |
| --- | --- |
| Brand | `--och-hue`, `--och-color-brand`, `-brand-hover`, `-brand-strong`, `-brand-soft`, and teal `--och-color-accent` |
| Surfaces | `--och-color-surface`, `-surface-raised`, `-surface-muted`, `-surface-sidebar`, `-overlay`, and `-backdrop` |
| Content and edges | `--och-color-text`, `-text-strong`, `-text-muted`, `-border`, and `-border-strong` |
| Status | paired foreground and `-soft` tokens for `success`, `warning`, `danger`, `info`, and `refund` |
| Type | system `--och-font-sans`, `--och-font-mono`, caption/small sizes, and fluid size steps `0` through `2` |
| Geometry | spacing steps `0` through `5`, small/default/large radii, border width, shadow, content maximum, sidebar size, and control minimum |
| Motion | `--och-motion-fast` and `--och-motion-standard` |

Color values are OKLCH translations of the source zinc, emerald, teal, amber,
red, blue, and purple values. `:root` declares `color-scheme: dark`, so operating
system preference cannot silently replace the product default. A root
`data-theme="light"` selects the explicit light values through `light-dark()`;
`data-theme="dark"` returns to the canonical dark treatment. Both schemes use
the same semantic token names.

The reference contrast audit uses the underlying sRGB source values before
their OKLCH translation:

| Role | Dark contrast on zinc-950 | Light contrast on white |
| --- | ---: | ---: |
| Primary text: zinc-100 / zinc-900 | 18.10:1 | 17.72:1 |
| Secondary text: zinc-400 / zinc-600 | 7.76:1 | 7.73:1 |
| Brand and focus: emerald-400 / emerald-700 | 10.35:1 | 5.48:1 |
| Strong boundary: zinc-500 | 4.12:1 | 4.83:1 |

Zinc-800 is intentionally a quiet separator and must not be the sole boundary
of an interactive control. Controls that require a visible perimeter use
`--och-color-border-strong`; focus uses the high-contrast brand outline. Text
and status foregrounds meet at least 4.5:1 against their canonical canvas.
More-contrast mode strengthens all borders. Forced-colors mode maps the
semantic vocabulary to system colors and removes decorative shadow where
necessary. Reduced-motion mode sets motion durations to zero; motion is added
only inside `prefers-reduced-motion: no-preference`.

### Status language

Status is semantic, not a palette-picker. Use the same meaning in both apps:

| Tone | Meaning |
| --- | --- |
| `success` | Active, authenticated, available, or completed successfully |
| `warning` | Action required, degraded, nearing a limit, or unavailable without data loss |
| `danger` | Denied, failed, destructive, suspended, or stopped |
| `info` | Neutral counts, metering context, synchronizing, or explanatory state |
| `refund` | Ledger reversal or refund, where distinct financial classification is necessary |
| muted surface/text | Inactive, unknown, secondary, or not-applicable state |

Never communicate state by color alone. Pair tone with concise text and, where
useful, a native state or value element. Dynamic status changes use `role="status"`
or a polite live region; actionable failures use `role="alert"` when immediate
announcement is warranted.

## Global CSS and Shadow DOM contract

There are three layers of styling responsibility:

1. `frontend/packages/styles/global.css` owns the reset, shared tokens, themes,
   typography, shell appearance, application layout, and shared Shadow Part
   rules.
2. Each app's `src/global.css` owns only application-specific tokens and rules
   for parts that its root component exports.
3. Lit `static styles` owns internal display structure, responsive state, and
   component mechanics. It consumes inherited custom properties; it does not
   introduce raw brand colors, typography, spacing, radii, shadows, or motion.

Global selectors cannot pierce Shadow DOM. Custom properties cross the boundary
by inheritance, and `part` plus `exportparts` creates deliberate styling
channels. A new nested component must therefore expose a minimal stable part
surface, and every intervening host must forward the parts its global
stylesheet addresses. Do not rely on element internals, generated class names,
test IDs, or selector chains into third-party Shadow DOM.

No inline `style` attributes, CSS-in-JS, Tailwind utilities, or unlayered global
overrides are allowed. Use logical properties, container-aware layout, and
progressive enhancement. Component state belongs in semantic attributes such
as `aria-current`, `disabled`, and bounded `data-tone` values, not dynamically
assembled presentation classes.

## Markup and interaction

- Use native landmarks and elements before custom roles or generic containers.
- Each rendered application has exactly one `main` and one page `h1`; route
  sections begin at `h2` and preserve heading order.
- Links navigate and buttons act. Every control has a programmatic label and at
  least the shared minimum target size.
- Tables have captions and scoped headers. Horizontally scrollable table regions
  are labelled and keyboard focusable.
- Exact identifiers, quantities, and financial values use `code`, `data`, or
  tabular monospace presentation as appropriate. Dates use `time`; untrusted
  bidirectional text uses `bdi`.
- Use native `dialog` for confirmations and one-time secrets. Move focus into
  an opened dialog, return it predictably, and erase secret state on close.
- Preserve native browser navigation, keyboard operation, visible focus,
  zoom/reflow, high contrast, and reduced motion.

## TypeScript and effects

- Strict TypeScript enables `exactOptionalPropertyTypes` and
  `noUncheckedIndexedAccess` plus Lit's required decorator configuration.
- Untrusted API, URL, storage, and event data is decoded with Effect Schema.
- Services are provided through Effect layers and Lit context. Components do
  not call `fetch` directly.
- Fallible application workflows return typed Effect errors. The browser has
  one managed runtime.
- Route IDs and navigation metadata are immutable domain values; rendering
  switches exhaustively over those values.

## Extending the frontend

When adding a route, component, status, or theme:

1. extend the typed route/navigation model and its parser tests;
2. start with semantic HTML and verify heading and landmark ownership;
3. reuse an existing semantic token before proposing a new token;
4. add structural component CSS only, then expose the smallest useful part
   contract and forward it through every host boundary;
5. update app global CSS for visual treatment and regenerate custom-elements
   manifests when a public slot, attribute, event, or part changes;
6. test keyboard, focus, History API, narrow and wide layouts, light and dark
   schemes, forced colors, contrast, and reduced motion; and
7. run the app-specific coverage gate as well as shared frontend quality.

Do not copy a Pymthouse component to accelerate a change. Re-express a useful
interaction using Lit, Effect, native semantics, the shared token vocabulary,
and the clearinghouse's own domain language.

## Tests

Pure route, domain, and Effect workflow tests run in Node. Component semantics,
behavior, and automated accessibility run in real Chromium through Vitest
browser mode. Critical journeys and responsive assertions run in Playwright
against production builds, with Firefox and WebKit smoke coverage. Backend and
each frontend independently enforce at least 85% lines, statements, functions,
and branches; generated custom-elements metadata must also be current.
