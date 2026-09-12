import { LitElement, css, html } from "lit"
import { customElement, state } from "lit/decorators.js"
import {
  readDisplayDenomination,
  setDisplayDenomination,
  subscribeDisplayDenomination,
  type DisplayDenomination
} from "./denomination.js"

/**
 * Same-origin display denomination preference for clearinghouse monetary values.
 *
 * @csspart field - Label and native selector group.
 * @csspart label - Visible selector label.
 * @csspart select - Native denomination selector.
 */
@customElement("och-denomination-control")
export class OchDenominationControl extends LitElement {
  static styles = css`
    :host { display: inline-block; }
    label { display: flex; align-items: center; gap: var(--och-space-1, 0.5rem); }
    select { min-block-size: var(--och-control-min-block-size, 2.75rem); }
  `

  @state() private denomination: DisplayDenomination = "wei"
  private unsubscribe: (() => void) | undefined

  connectedCallback(): void {
    super.connectedCallback?.()
    this.denomination = readDisplayDenomination()
    this.unsubscribe = subscribeDisplayDenomination((value) => { this.denomination = value })
  }

  disconnectedCallback(): void {
    this.unsubscribe?.()
    this.unsubscribe = undefined
    super.disconnectedCallback?.()
  }

  private change(event: Event): void {
    if (!(event.currentTarget instanceof HTMLSelectElement)) return
    setDisplayDenomination(event.currentTarget.value === "eth" ? "eth" : "wei")
  }

  protected render() {
    return html`
      <label part="field" for="display-denomination">
        <span part="label">Display</span>
        <select id="display-denomination" part="select" aria-label="Currency denomination" .value=${this.denomination} @change=${this.change}>
          <option value="wei">Wei</option>
          <option value="eth">ETH</option>
        </select>
      </label>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "och-denomination-control": OchDenominationControl
  }
}
