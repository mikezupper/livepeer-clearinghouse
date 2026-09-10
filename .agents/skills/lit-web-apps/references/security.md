# Security

Lit is safe by default: template strings are parsed as HTML **once, without data**; interpolated values land as text/attribute values and are never parsed as markup. Injection requires opting out. Keep the opt-outs rare and audited.

## The unsafe surface — rules

| API | Rule |
|---|---|
| `unsafeHTML` / `unsafeSVG` | Never user- or API-supplied content. If rendering user markdown/rich text, sanitize first (DOMPurify) and wrap the sanitizer in one audited helper — no raw `unsafeHTML` calls at call sites. |
| `unsafeStatic` / `literal` | Compile-time constants only. `unsafeStatic(userValue)` is direct HTML injection. |
| `unsafeCSS` | Trusted constants only (CSS exfiltration is real). |
| `.innerHTML`, `insertAdjacentHTML` | Don't. Use templates. |

Grep-able invariant for CI: `unsafeHTML(` appears only in `src/lib/sanitized-html.ts`.

## Trusted Types

lit-html ships an internal Trusted Types policy (`lit-html`) for its template parsing, so Lit apps can run under the strictest DOM-injection CSP:

```
Content-Security-Policy: require-trusted-types-for 'script'; trusted-types lit-html app-sanitizer;
```

Any remaining `innerHTML`-style sink in app code then fails loudly unless it goes through your named policy — this turns the rules above into runtime enforcement.

## SSR-specific

- **Serialized state must be script-safe.** `JSON.stringify` output containing `</script>` breaks out of the data block. Always escape when embedding:

```ts
const safeJson = (data: unknown) =>
  JSON.stringify(data).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
```

- **\u2026and must be emitted as raw HTML, not a binding.** Template bindings are HTML-escaped even in `<script>` context, and browsers/crawlers do not entity-decode script content \u2014 a plain `${safeJson(data)}` binding produces `&quot;`-riddled JSON that fails `JSON.parse` and invalidates JSON-LD. Emit the whole element through one audited `unsafeHTML` call (safe because `safeJson` output contains no `<`):

```ts
const jsonScript = (attrs: string, data: unknown) =>
  unsafeHTML(`<script ${attrs}>${safeJson(data)}</script>`);
// ${jsonScript('type="application/json" id="__DATA__"', data)}
```

Add a test asserting the emitted block contains no `&quot;` and round-trips through `JSON.parse` (see rendering-modes.md). The client's `JSON.parse` is unaffected by the `<` escapes.

- Server-only templates escape interpolations like normal Lit templates — but anything concatenated *outside* a template (headers, redirects) follows normal injection rules.
- Never render secrets into HTML or `__DATA__`; the render service should receive only view data from the API layer (architecture.md's service boundary is also the data-minimization boundary).
- Streaming + CSP nonces: generate the nonce per request in the route handler and pass it into the document template for the entry `<script>` tag.

## App-level checklist

- `fetch` with `credentials: 'same-origin'` default; CSRF tokens on mutating routes (native form posts included — SSR forms work pre-hydration, so they need CSRF protection too).
- Sanitize `href` values that come from data (`javascript:` URLs pass attribute bindings — validate protocol in the service layer or a `safeHref()` helper).
- `rel="noopener"` on `target="_blank"` links.
- Dependency hygiene: labs packages are pinned to minors (see README maintenance playbook); `npm audit` in CI.
