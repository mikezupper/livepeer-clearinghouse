# Frontend engineering

`user-web` and `admin-web` are independent Lit/Vite applications served from `/` and `/admin/`. Both use Effect for typed fallible workflows and Effect Schema for untrusted JSON.

## Information architecture

| Application | Routes |
| --- | --- |
| User | Overview, Network, Cost estimator, Workloads, API credentials, Usage & cost, Profile |
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

The Cost estimator consumes current offers rather than maintaining a second price catalog. Its semantic form adapts to `fixed`, `seconds`, `pixel`, and `720p-pixel-seconds` units and uses exact integer/rational arithmetic with ceiling division. The estimate records user assumptions only; creating access snapshots the selected offer, while Usage & cost remains authoritative for signer-measured results.

The shared header denomination control defaults to Wei and can present Wei-denominated values as ETH across both applications. The preference is stored under one same-origin browser key and synchronized between open tabs. Conversion is presentation-only: APIs, persisted values, quote snapshots, and signer inputs remain exact Wei integers or rationals. ETH rendering uses `1 ETH = 10^18 wei`, never floating-point arithmetic; repeating rates are marked approximate and retain their exact Wei rational alongside the display value. Prices in other currencies are not converted.

## Validation

Run `make quality-frontend`, `make test-frontend`, and `make test-browser`. Each app and shared package independently enforces all four 85% coverage metrics. Real-browser tests exercise Shadow DOM pagination and large fixtures verify that a page never creates more than the configured item count in the DOM. Visual baselines cover desktop dark, desktop light, mobile content, and mobile navigation.
