# Localization — @lit/localize (stable)

Runtime is ~1.3 KiB min+brotli. Two modes; choose per app:

| | Runtime mode | Transform mode |
|---|---|---|
| How | One generated JS module per locale, loaded dynamically | `lit-localize build` emits a **complete app build per locale**, `msg()` compiled away |
| Locale switch | `setLocale()` — no reload | Page reload (serve the right build) |
| Overhead | 1.3 KiB + slight render cost | Zero runtime overhead |
| Fits | SPAs, user-switchable locale | SSG/SSR sites where locale is in the URL (`/de/…`) — pairs best with this stack's SSG mode |

## Authoring

```ts
import { msg, str } from '@lit/localize';
import { localized } from '@lit/localize/localized-decorator.js';

@localized()                         // re-renders on locale change (runtime mode)
@customElement('greeting-card')
class GreetingCard extends LitElement {
  @property() name = '';
  render() {
    return html`
      <h1>${msg('Welcome')}</h1>
      <p>${msg(str`Hello, ${this.name}!`)}</p>              <!-- expressions need str`` -->
      <p>${msg(html`Read the <a href="/docs">docs</a>`, { desc: 'footer link' })}</p>
    `;
  }
}
```

- Plain string, `str\`\`` (with expressions), or `html\`\`` inside `msg()`. Untagged template with expressions = extraction error.
- IDs are content-hashed automatically; identical content dedupes. Provide `{ id }` only when the same text needs different translations; `{ desc }` gives translators context.
- Use BCP 47 locale codes.

## Config + XLIFF workflow

```jsonc
// lit-localize.json
{
  "sourceLocale": "en",
  "targetLocales": ["de", "ja"],
  "tsConfig": "tsconfig.json",
  "output": { "mode": "runtime", "outputDir": "src/generated/locales", "localeCodesModule": "src/generated/locale-codes.ts" },
  "interchange": { "format": "xliff", "xliffDir": "xliff" }
}
```

1. `lit-localize extract` → `xliff/<locale>.xlf` (XLIFF 1.2, `<trans-unit>` per message)
2. Translators fill `<target>`; files return to `xliff/`
3. `lit-localize build` → locale modules (runtime) or per-locale app builds (transform)

## Runtime-mode wiring

```ts
import { configureLocalization } from '@lit/localize';
import { sourceLocale, targetLocales } from './generated/locale-codes.js';

export const { getLocale, setLocale } = configureLocalization({
  sourceLocale,
  targetLocales,
  loadLocale: (locale) => import(`./generated/locales/${locale}.js`),
});
// setLocale('de') — @localized() components re-render; `lit-localize-status` event tracks loading
```

## SSR interaction

Set the locale on the server **before rendering** (from URL prefix or `Accept-Language`) so server HTML matches what the client hydrates. With transform mode + SSG, prerender each locale's build under its URL prefix (`/de/…`) — zero client i18n cost. Put locale in `__DATA__` so the client configures the same locale before hydration.
