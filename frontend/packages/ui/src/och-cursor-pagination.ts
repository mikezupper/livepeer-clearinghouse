import { LitElement, css, html, type TemplateResult } from "lit"
import { customElement, property } from "lit/decorators.js"
import type { CursorDirection } from "./pagination.js"

@customElement("och-cursor-pagination")
export class OchCursorPagination extends LitElement {
  static styles = css`
    :host, nav, menu { display: flex; align-items: center; }
    nav, menu { gap: var(--och-pagination-gap, 0.75rem); }
    menu { margin: 0; padding: 0; list-style: none; }
  `

  @property({ type: Boolean }) hasPrevious = false
  @property({ type: Boolean }) hasNext = false
  @property({ type: Boolean }) busy = false
  @property({ type: Number }) page = 1

  private move(direction: CursorDirection): void {
    this.dispatchEvent(new CustomEvent("cursor-page-change", {
      detail: { direction }, bubbles: true, composed: true
    }))
  }
  private previous(): void { this.move("previous") }
  private next(): void { this.move("next") }

  protected render(): TemplateResult {
    return html`<nav part="navigation" aria-label="Collection pages">
      <p part="status" aria-live="polite">Page ${this.page}</p>
      <menu part="actions">
        <li><button part="button previous" ?disabled=${this.busy || !this.hasPrevious} @click=${this.previous}>Previous</button></li>
        <li><button part="button next" ?disabled=${this.busy || !this.hasNext} @click=${this.next}>Next</button></li>
      </menu>
    </nav>`
  }
}

declare global {
  interface HTMLElementTagNameMap { "och-cursor-pagination": OchCursorPagination }
  interface HTMLElementEventMap {
    "cursor-page-change": CustomEvent<{ readonly direction: CursorDirection }>
  }
}
