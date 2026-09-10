# Frontend engineering

## Applications

`admin-web` and `user-web` are independent Vite applications sharing domain
schemas, Effect services, design tokens, and semantic Lit components. Both are
client-rendered authenticated dashboards; the Python service owns business
logic and data.

## Markup

- Use native semantic elements before custom roles or generic containers.
- Exactly one `main` and one page `h1`; preserve heading order and landmarks.
- Links navigate and buttons act. Every control has a programmatic label.
- Tables have captions and scoped headers. Status changes use appropriate live
  regions. Keyboard operation and focus visibility are mandatory.
- Inline styles and presentation-only markup are prohibited.

## Styling contract

`global.css` is the only application-global stylesheet. It declares cascade
layers, reset, typed design tokens, themes, typography, page layout, and rules
targeting exported Shadow Parts.

Shadow DOM prevents arbitrary global selectors from entering a component.
Components therefore:

- inherit global custom properties;
- use those properties for every brand color, type, spacing, radius, shadow,
  motion, and breakpoint decision;
- expose intentional customization points with `part` and `exportparts`; and
- keep component styles limited to internal structure and state.

No component duplicates a raw brand value. Modern CSS is progressively
enhanced according to the repository skill's support tiers.

## TypeScript and effects

- Strict TypeScript enables `exactOptionalPropertyTypes` and
  `noUncheckedIndexedAccess` plus Lit's required decorator configuration.
- Untrusted API, URL, storage, and event data is decoded with Effect Schema.
- Services are provided through Effect layers and Lit context. Components do
  not call `fetch` directly.
- Fallible application workflows return typed Effect errors. The browser has
  one managed runtime.

## Tests

Pure domain and Effect workflow tests run in Node. Component and accessibility
tests run in real Chromium through Vitest browser mode. Critical journeys run
in Playwright against the Compose stack. Each app independently enforces 85%
lines, statements, functions, and branches.
