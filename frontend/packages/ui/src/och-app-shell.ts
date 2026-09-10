import { LitElement, css, html } from "lit"
import { customElement, property } from "lit/decorators.js"

@customElement("och-app-shell")
export class OchAppShell extends LitElement {
  static styles = css`
    :host { display: block; }
    header, main, footer { display: block; }
  `

  @property() heading = "Open Clearinghouse"
  @property() summary = "Vendor-neutral walletless Livepeer network spend."
  @property({ attribute: "navigation-label" }) navigationLabel = "Primary"

  protected render() {
    return html`
      <a part="skip-link" href="#main">Skip to main content</a>
      <header part="header">
        <p part="brand" translate="no">Livepeer Clearinghouse</p>
        <nav part="navigation" aria-label=${this.navigationLabel.trim() || "Primary"}>
          <slot name="navigation"></slot>
        </nav>
      </header>
      <main part="main" id="main">
        <hgroup part="heading-group">
          <h1 part="title">${this.heading}</h1>
          <p part="summary">${this.summary}</p>
        </hgroup>
        <slot></slot>
      </main>
      <footer part="footer">
        <p part="footer-note"><small>Open source software from Livepeer.</small></p>
      </footer>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "och-app-shell": OchAppShell
  }
}
