# Testing — Vitest Browser Mode

Web components need a **real browser**: real shadow DOM, real custom element upgrades, real constructable stylesheets. Never jsdom/happy-dom. Vitest 4 browser mode is stable and lists Lit as an officially supported target.

## Setup

```ts
// vitest.config.ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    browser: {
      enabled: true,
      provider: 'playwright',
      headless: true,
      instances: [{ browser: 'chromium' }],   // add firefox/webkit for cross-engine CI
    },
    include: ['src/**/*.test.ts'],
  },
});
```

Dev deps: `vitest`, `@vitest/browser`, `playwright`, `@open-wc/testing` (framework-agnostic helpers that work under Vitest).

## Component test pattern

```ts
import { html } from 'lit';
import { fixture, oneEvent, expect } from '@open-wc/testing';
import { describe, it } from 'vitest';
import './user-card.js';
import type { UserCard } from './user-card.js';

describe('user-card', () => {
  it('renders the name and computes tier', async () => {
    // fixture() mounts and awaits updateComplete
    const el = await fixture<UserCard>(html`<user-card name="Ada" .score=${95}></user-card>`);
    expect(el.shadowRoot!.querySelector('h2')!.textContent).to.include('Ada');
    expect(el.shadowRoot!.querySelector('h2')!.textContent).to.include('gold');
  });

  it('reacts to property changes', async () => {
    const el = await fixture<UserCard>(html`<user-card></user-card>`);
    el.score = 30;
    await el.updateComplete;                          // ALWAYS await before asserting
    expect(el.shadowRoot!.textContent).to.include('standard');
  });

  it('fires item-selected', async () => {
    const el = await fixture<UserCard>(html`<user-card></user-card>`);
    setTimeout(() => el.shadowRoot!.querySelector('button')!.click());
    const ev = await oneEvent(el, 'item-selected');
    expect(ev.detail.id).to.exist;
  });

  it('is accessible', async () => {
    const el = await fixture<UserCard>(html`<user-card name="Ada"></user-card>`);
    await expect(el).to.be.accessible();              // axe-core via chai-a11y-axe
  });
});
```

## Rules & patterns

- `await el.updateComplete` after every state change; `await el.getUpdateComplete()` semantics apply when a component overrides it to await children.
- Query through `el.shadowRoot!` — piercing helpers hide real encapsulation bugs.
- Semantic DOM assertions: `expect(el).shadowDom.to.equal('<h2>Ada (gold)</h2>…')` — diffs ignore comments/whitespace/lit markers.
- Interaction: prefer Vitest's `page`/`userEvent` from `@vitest/browser/context` for real input events (keyboard, pointer) over synthetic `.click()`.
- Controllers: test through a minimal host element fixture, not in isolation.
- Signals/stores: reset module-level signals in `beforeEach` (export a `reset()` from each state module).
- Visual regression: Vitest 4 browser mode supports screenshot diffing when a component's rendering is layout-critical.

## SSR testing

Two levels, both cheap:

1. **Node string test** (fast, most coverage): render with `@lit-labs/ssr` and assert on the HTML — catches "component breaks on server" (DOM access in constructor/willUpdate/render) immediately.

```ts
// runs in a plain node vitest project (separate vitest workspace, environment: 'node')
import { render } from '@lit-labs/ssr';
import { collectResult } from '@lit-labs/ssr/lib/render-result.js';

it('server-renders without touching the DOM', async () => {
  const out = await collectResult(render(html`<user-card name="Ada"></user-card>`));
  expect(out).toContain('shadowrootmode="open"');
  expect(out).toContain('Ada');
});
```

Assertion trap: the renderer emits **both** the legacy `shadowroot` and final `shadowrootmode` attributes (`<template shadowroot="open" shadowrootmode="open">`) — assert on `'shadowrootmode="open"'` alone, never on the full `'<template shadowrootmode'` substring.

If pages embed JSON (`__DATA__`, JSON-LD), also assert the emitted block contains no `&quot;` and round-trips through `JSON.parse` — this catches the binding-escapes-JSON bug (security.md).

**Node-env tests are a first-class tier for the data layer.** Signals stores, services, and aggregation logic import `lit`/`@lit-labs/signals` cleanly in plain-Node Vitest (lit's node export conditions) — hundreds of assertions in ~250ms, no browser download. Structure state as testable modules and cover it here; reserve browser mode for actual DOM behavior.

2. **Full hydration round-trip**: `@lit-labs/testing` provides `ssrFixture` (server-render → load in browser → hydrate) but is Web Test Runner-only. Options: keep a small WTR suite just for hydration smoke tests of the top pages, or approximate in Vitest by asserting the node-rendered HTML parses with DSD and the component hydrates via an e2e (Playwright) check. Either is acceptable; don't skip hydration coverage of the app shell.

Use a Vitest **workspace** to keep the two environments separate: `projects: [{ test: { name: 'browser', browser: {...} } }, { test: { name: 'ssr', environment: 'node', include: ['src/**/*.ssr.test.ts'] } }]`.
