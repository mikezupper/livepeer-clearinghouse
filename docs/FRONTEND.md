# Frontend engineering

`user-web` and `admin-web` are independent Lit/Vite applications served from `/` and `/admin/`. Both use Effect for typed fallible workflows and Effect Schema for untrusted JSON.

## Information architecture

| Application | Routes |
| --- | --- |
| User | Overview, Network, Cost estimator, Workloads, Account API credentials, Usage & cost, Profile |
| Admin | Overview, Users & accounts, Workloads, Usage, Operations |

Unknown paths return to Overview. Native links plus the History API preserve browser navigation. The shared `och-app-shell` provides one main landmark, responsive navigation, skip link, focus management, and a footer.

Every data collection uses the shared `och-cursor-pagination` control. It
provides explicit Previous and Next actions, announces the current page, keeps
the opaque current cursor in the route query string, and retains an in-memory
back-stack for earlier cursors. Pages render at most 50 records by default.
Overview cards consume constant-size SQL summary endpoints instead of deriving
totals from hidden collection downloads. Network offer filters are part of the
cursor identity; a refreshed discovery generation restarts navigation with an
explicit notice.

## Semantic and styling contract

Use native headings, sections, articles, navigation, lists, forms, labels, tables/captions, definition lists, time, code, and dialog before generic elements or ARIA roles. Links navigate; buttons act. Every page has one shell-owned `h1`; route content begins at `h2`.

No inline style attributes, CSS-in-JS, Tailwind utilities, or presentation-only DOM are allowed. `frontend/packages/styles/global.css` owns shared zinc/emerald tokens, typography, themes, shell layout, focus, contrast, reduced motion, and forced colors. Each app's `global.css` owns its stable application parts. Component `static styles` defines only encapsulated structure using inherited custom properties.

CSS selectors cannot cross Shadow DOM. Custom properties inherit through it. Elements that need global treatment expose `part`, and every nested host forwards supported names with `exportparts`. Slotted navigation lists and labels expose their own parts. This explicit surface lets the global stylesheet control the UI without depending on private internals.

The visual language reinterprets the useful Pymthouse console concepts: compact data density, zinc surfaces, emerald primary state, restrained borders, task-oriented sidebar navigation, responsive mobile drawer, and dark-first presentation. It does not copy Pymthouse code or enterprise workflows.

The Cost estimator consumes current offers rather than maintaining a second price catalog. Its semantic form adapts to `fixed`, `seconds`, `pixel`, and `720p-pixel-seconds` units and uses exact integer/rational arithmetic with ceiling division. The estimate records user assumptions only. A separately labelled optional maximum-spend input becomes an immutable enforced workload ceiling; it is never inferred from the estimate. Usage & cost distinguishes signer-reported fee, authorized-but-unreconciled pending exposure, ceiling, and remaining spend.

The shared header denomination control defaults to Wei and can present Wei-denominated values as ETH across both applications. The preference is stored under one same-origin browser key and synchronized between open tabs. Display conversion is presentation-only; the spend-ceiling form accepts the selected denomination and converts it exactly to a Wei integer at the boundary. APIs, persisted values, quote snapshots, and signer inputs remain exact Wei integers or rationals. ETH rendering and parsing use `1 ETH = 10^18 wei`, never floating-point arithmetic; repeating rates are marked approximate and retain their exact Wei rational alongside the display value. Prices in other currencies are not converted.

## Content contract

[Product content design](product-specs/content-design.md) defines the shared
hierarchy, vocabulary, help layers, and state-writing rules. Route metadata is
the source of truth for navigation labels and shell summaries; component
templates own the copy that is specific to their controls and data regions.
Documentation describes patterns and canonical terms, not a second copy of
every interface string.

Keep task context and consequences visible. Put supplemental explanations in a
native `details` disclosure. A contextual-help tooltip may define a short term,
but it must be keyboard reachable, dismissible, and available to assistive
technology; it must not be the only source of instructions. Associate field
hints and validation errors with their controls using `aria-describedby` and
stable IDs. Preserve the visible `label` as the control's accessible name.

Loading and successful updates use polite status announcements. Failures that
require attention use alerts and retain a recovery action. Do not announce
static introductory prose or move focus merely because data refreshed.

## Validation

Run `make quality-frontend`, `make test-frontend`, and `make test-browser`. Each app and shared package independently enforces all four 85% coverage metrics. Component content and accessible-name assertions live beside the applications in `frontend/apps/*/src/*.test.ts` and beside shared components in `frontend/packages/ui/src/*.test.ts`. Browser journeys and Axe checks live in `frontend/e2e/clearinghouse.journey.spec.ts` and `frontend/e2e/clearinghouse.accessibility.spec.ts`. Real-browser tests exercise Shadow DOM pagination and large fixtures verify that a page never creates more than the configured item count in the DOM. Visual baselines cover desktop dark, desktop light, mobile content, and mobile navigation.
