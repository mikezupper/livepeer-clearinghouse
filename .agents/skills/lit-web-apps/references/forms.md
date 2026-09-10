# Forms — Form-Associated Custom Elements (FACE)

Custom form controls must participate in native forms — value submission, validation, reset, disabled state, labels. The platform primitive is **ElementInternals**; no form library required. Every input-like component in the design system follows this pattern.

## The pattern

```ts
@customElement('app-input')
export class AppInput extends LitElement {
  static formAssociated = true;                       // opt in to form participation
  static shadowRootOptions = { ...LitElement.shadowRootOptions, delegatesFocus: true };

  #internals = this.attachInternals();                // safe in constructor field — shimmed for SSR

  @property() name = '';
  @property() value = '';
  @property({ type: Boolean }) required = false;

  willUpdate(changed: PropertyValues<this>) {
    if (changed.has('value') || changed.has('required')) {
      this.#internals.setFormValue(this.value);       // what the <form> submits
      this.#internals.setValidity(
        { valueMissing: this.required && !this.value },
        'This field is required',
        this.#input,                                  // anchor for the validation popup
      );
    }
  }

  render() {
    return html`
      <input
        .value=${live(this.value)}
        @input=${(e: InputEvent) => (this.value = (e.target as HTMLInputElement).value)}
        aria-label=${this.#internals.ariaLabel ?? nothing}
      >`;
  }

  get #input() { return this.renderRoot?.querySelector('input') ?? undefined; }

  // Form lifecycle callbacks
  formResetCallback() { this.value = ''; }
  formDisabledCallback(disabled: boolean) { this.toggleAttribute('data-disabled', disabled); }
  formStateRestoreCallback(state: unknown) {           // spec: File | string | FormData | null
    if (typeof state === 'string') this.value = state; // bfcache/autofill restore
  }
}
```

**Anchor timing:** the validation anchor (`this.#input`) is `undefined` until first render — a submit before any property change would report validity with no anchor. Re-sync once after first render:

```ts
firstUpdated() { this.requestUpdate('value'); }  // or re-run the setValidity block in updated()
```

(Client-only lifecycle like `updated()` is also a legitimate home for anchor-dependent validity — it never runs on the server, so no `isServer` guard needed.)

**Reflected FACE properties:** pair `reflect: true` with `useDefault: true` (Lit 3.3+) on properties like `name` — otherwise the `''` default reflects as `name=""` on upgrade.

Now `<form>` sees it natively: it appears in `FormData`, blocks submit when invalid, resets with the form, and `<label for>` / implicit labels work.

## Validation API

- `#internals.setValidity(flags, message, anchor)` — flags: `valueMissing`, `typeMismatch`, `patternMismatch`, `tooLong/tooShort`, `rangeOverflow/Underflow`, `stepMismatch`, `badInput`, `customError`. Empty flags `{}` = valid.
- `#internals.checkValidity()` / `reportValidity()` / `validationMessage` mirror native inputs.
- Style states with `:host(:invalid)`, `:host(:user-invalid)` (after interaction — prefer this to avoid red-on-load), `:host(:disabled)`.

## Custom states — `:state()`

```ts
this.#internals.states.add('loading');    // later: .delete('loading')
```
```css
:host(:state(loading)) { opacity: 0.6; }          /* inside */
app-input:state(loading) { … }                     /* consumers, outside */
```
Prefer custom states over reflected attributes for style-only state — no attribute churn, no API surface.

## Accessibility via ElementInternals

- Default semantics without sprouting attributes: `this.#internals.role = 'combobox'`, `this.#internals.ariaExpanded = 'false'`. These are defaults — author-set ARIA attributes on the host win.
- Cross-root ARIA (`aria-labelledby` across shadow boundaries) still can't reference IDs in another root: keep label + control in the same root, use `delegatesFocus`, or accept a `label` property and render it internally.
- `delegatesFocus: true` on every focusable control: host click focuses the inner input, `:focus-visible` styling on `:host(:focus-visible)` works.

## Whole-form composition

- A `<form>` in a page component works natively with FACE controls — read with `new FormData(form)`, validate with `form.reportValidity()`, submit via `@submit` handler + `fetch` (or let it POST natively for no-JS SSR pages: SSR forms with `action`/`method` work before hydration — free progressive enhancement).
- Form-level state (multi-step wizards, dirty tracking) → a signals module or a `FormController` reactive controller owning `FormData` + validity aggregation.
- **`@lit-labs/forms`** (0.1.0, first published Dec 2025): first-party form binding/validation layer — too new to pin; evaluate on the quarterly update pass (see README maintenance playbook) and adopt if it has stabilized.

## SSR notes

- `attachInternals()` is shimmed by `@lit-labs/ssr-dom-shim` — constructing FACE components on the server is safe; validity/form APIs are inert there. Anchor-element lookups (`this.renderRoot.querySelector`) must stay out of server paths (`willUpdate` guard shown above only calls setValidity with an element that's `undefined` server-side — that's fine).
- Native `<form action method>` in SSR/SSG output means forms function before JS arrives.
