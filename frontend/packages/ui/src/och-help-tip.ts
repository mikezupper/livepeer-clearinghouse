import { LitElement, css, html, type TemplateResult } from "lit"
import { customElement, property } from "lit/decorators.js"

/**
 * Compact, optional context for a nearby term or value.
 *
 * Essential instructions and action consequences must remain visible outside
 * this component. Slotted help is available by click, keyboard, and touch
 * through the native popover interaction.
 *
 * @csspart trigger - Button that opens the contextual help.
 * @csspart popover - Native popover containing the slotted explanation.
 * @slot - A short definition or supplemental explanation.
 */
@customElement("och-help-tip")
export class OchHelpTip extends LitElement {
  static styles = css`
    :host { display: inline-flex; vertical-align: middle; }
    button {
      display: inline-grid;
      place-items: center;
      min-inline-size: var(--och-help-trigger-size, 1.5rem);
      min-block-size: var(--och-help-trigger-size, 1.5rem);
      padding: 0;
      color: var(--och-help-color, var(--och-color-text-muted));
      background: var(--och-help-background, transparent);
      border: var(--och-border-width, 1px) solid var(--och-help-border, var(--och-color-border-strong));
      border-radius: 50%;
      font: inherit;
      font-size: var(--och-help-font-size, 0.75rem);
      font-weight: 700;
      cursor: help;
    }
    [popover] {
      position: fixed;
      max-inline-size: min(var(--och-help-max-size, 24rem), calc(100vi - 2rem));
      padding: var(--och-space-2, 0.75rem);
      color: var(--och-color-text);
      background: var(--och-color-surface-raised);
      border: var(--och-border-width, 1px) solid var(--och-color-border-strong);
      border-radius: var(--och-radius-small, 0.5rem);
      box-shadow: var(--och-shadow);
      line-height: 1.5;
    }
    [popover]::backdrop { background: transparent; }
  `

  @property() term = "this term"

  protected render(): TemplateResult {
    return html`
      <button
        type="button"
        part="trigger"
        popovertarget="context-help"
        aria-label=${`Help: ${this.term}`}
      >?</button>
      <span id="context-help" part="popover" popover="auto" role="note"><slot></slot></span>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap { "och-help-tip": OchHelpTip }
}
