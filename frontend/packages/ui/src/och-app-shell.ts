import { LitElement, css, html, svg } from "lit"
import { customElement, property, state } from "lit/decorators.js"

/**
 * Responsive semantic application shell shared by clearinghouse consoles.
 *
 * @slot - Main page content.
 * @slot navigation - Primary navigation list.
 * @slot context - Compact page or tenant context for the top bar.
 * @slot utility - Session and utility actions for the top bar.
 * @csspart skip-link - Keyboard skip link.
 * @csspart shell - Overall shell layout.
 * @csspart sidebar - Persistent desktop and off-canvas mobile sidebar.
 * @csspart header - Backward-compatible sidebar header.
 * @csspart sidebar-header - Sidebar brand region.
 * @csspart brand - Clearinghouse wordmark group.
 * @csspart brand-name - Clearinghouse product name.
 * @csspart brand-accent - Livepeer brand accent within the product name.
 * @csspart brand-subtitle - Compact clearinghouse purpose line.
 * @csspart sidebar-close-button - Mobile navigation close control.
 * @csspart sidebar-close-icon - Decorative icon inside the mobile close control.
 * @csspart sidebar-close-label - Accessible text inside the mobile close control.
 * @csspart navigation - Primary navigation landmark.
 * @csspart navigation-backdrop - Mobile navigation dismiss target.
 * @csspart workspace - Main workspace column.
 * @csspart topbar - Context and utility bar.
 * @csspart menu-button - Mobile navigation toggle.
 * @csspart menu-icon - Decorative icon inside the mobile navigation toggle.
 * @csspart menu-label - Accessible text inside the mobile navigation toggle.
 * @csspart mobile-brand - Compact brand displayed in the mobile top bar.
 * @csspart context - Context slot wrapper.
 * @csspart utility - Utility slot wrapper.
 * @csspart main - Main content landmark.
 * @csspart heading-group - Page heading and summary group.
 * @csspart title - Page title.
 * @csspart summary - Page summary.
 * @csspart content - Default slot wrapper.
 * @csspart footer - Application footer.
 * @csspart footer-note - Application footer note.
 */
@customElement("och-app-shell")
export class OchAppShell extends LitElement {
  static styles = css`
    *,
    *::before,
    *::after {
      box-sizing: border-box;
    }

    :host {
      display: block;
      min-block-size: 100dvh;
    }

    [part~="shell"] {
      display: grid;
      grid-template-columns: var(--och-shell-sidebar-size, 16rem) minmax(0, 1fr);
      min-block-size: 100dvh;
    }

    [part~="sidebar"] {
      position: sticky;
      inset-block-start: 0;
      z-index: 2;
      display: flex;
      flex-direction: column;
      block-size: 100dvh;
      overflow-y: auto;
    }

    [part~="sidebar-header"],
    [part~="topbar"] {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    [part~="navigation"] {
      flex: 1;
    }

    [part~="workspace"] {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-inline-size: 0;
      min-block-size: 100dvh;
    }

    [part~="topbar"] {
      min-inline-size: 0;
    }

    [part~="context"],
    [part~="utility"] {
      display: flex;
      align-items: center;
      min-inline-size: 0;
    }

    [part~="main"] {
      display: block;
      min-inline-size: 0;
    }

    [part~="skip-link"],
    [part~="menu-button"] {
      align-items: center;
      min-block-size: var(--och-control-min-block-size, 2.75rem);
    }

    [part~="skip-link"] {
      display: inline-flex;
    }

    [part~="menu-button"],
    [part~="sidebar-close-button"],
    [part~="navigation-backdrop"] {
      display: none;
    }

    @media (max-width: 47.999rem) {
      [part~="shell"] {
        display: block;
      }

      [part~="sidebar"] {
        position: fixed;
        inset-block: 0;
        inset-inline-start: 0;
        inline-size: min(
          var(--och-shell-sidebar-size, 16rem),
          calc(100% - var(--och-space-4, 2rem))
        );
        translate: -110% 0;
        visibility: hidden;
      }

      :host([navigation-open]) [part~="sidebar"] {
        translate: 0;
        visibility: visible;
      }

      [part~="menu-button"] {
        display: inline-flex;
      }

      [part~="sidebar-close-button"] {
        display: inline-flex;
      }

      :host([navigation-open]) [part~="navigation-backdrop"] {
        position: fixed;
        inset: 0;
        z-index: 1;
        display: block;
        inline-size: 100%;
        block-size: 100%;
      }
    }
  `

  @property() heading = "Open Clearinghouse"
  @property() summary = "Vendor-neutral walletless Livepeer network spend."
  @property({ attribute: "navigation-label" }) navigationLabel = "Primary"
  @state() private navigationOpen = false

  private setNavigationOpen(open: boolean): void {
    this.navigationOpen = open
    this.toggleAttribute("navigation-open", open)
  }

  private toggleNavigation(): void {
    if (this.navigationOpen) {
      this.closeNavigation()
      return
    }
    this.setNavigationOpen(true)
    requestAnimationFrame(() => {
      const closeButton = this.renderRoot.querySelector("[part~='sidebar-close-button']")
      if (closeButton instanceof HTMLButtonElement) closeButton.focus()
    })
  }

  private closeNavigation(): void {
    if (!this.navigationOpen) return
    this.setNavigationOpen(false)
    requestAnimationFrame(() => {
      const button = this.renderRoot.querySelector("[part~='menu-button']")
      if (button instanceof HTMLButtonElement) button.focus()
    })
  }

  private handleNavigationClick(event: MouseEvent): void {
    if (event.composedPath().some((target) => target instanceof HTMLAnchorElement)) {
      this.closeNavigation()
    }
  }

  private handleKeydown(event: KeyboardEvent): void {
    if (event.key !== "Escape" || !this.navigationOpen) return
    this.closeNavigation()
  }

  protected render() {
    const navigationLabel = this.navigationLabel.trim() || "Primary"
    return html`
      <a part="skip-link" href="#main">Skip to main content</a>
      <div part="shell" @keydown=${this.handleKeydown}>
        <aside part="sidebar" aria-label=${navigationLabel}>
          <header part="header sidebar-header">
            <p part="brand" translate="no">
              <span part="brand-name"><span part="brand-accent">Livepeer</span> Clearinghouse</span>
              <small part="brand-subtitle">Identity &amp; Payments</small>
            </p>
            <button
              part="sidebar-close-button"
              type="button"
              aria-label="Close navigation"
              @click=${this.closeNavigation}
            >
              <svg part="sidebar-close-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6 18 18M18 6 6 18"></path>
              </svg>
              <span part="sidebar-close-label">Close</span>
            </button>
          </header>
          <nav
            id="primary-navigation"
            part="navigation"
            aria-label=${navigationLabel}
            tabindex="-1"
            @click=${this.handleNavigationClick}
          >
            <slot name="navigation"></slot>
          </nav>
        </aside>
        <button
          part="navigation-backdrop"
          type="button"
          aria-label="Close navigation"
          tabindex="-1"
          @click=${this.closeNavigation}
        ></button>
        <div part="workspace" .inert=${this.navigationOpen}>
          <header part="topbar">
            <button
              part="menu-button"
              type="button"
              aria-label=${this.navigationOpen ? "Close navigation" : "Open navigation"}
              aria-controls="primary-navigation"
              aria-expanded=${this.navigationOpen ? "true" : "false"}
              @click=${this.toggleNavigation}
            >
              <svg part="menu-icon" viewBox="0 0 24 24" aria-hidden="true">
                ${this.navigationOpen
                  ? svg`<path d="M6 6 18 18M18 6 6 18"></path>`
                  : svg`<path d="M4 6h16M4 12h16M4 18h16"></path>`}
              </svg>
              <span part="menu-label">${this.navigationOpen ? "Close navigation" : "Open navigation"}</span>
            </button>
            <p part="mobile-brand" translate="no"><span part="brand-accent">Livepeer</span> Clearinghouse</p>
            <div part="context"><slot name="context"></slot></div>
            <div part="utility"><slot name="utility"></slot></div>
          </header>
          <main part="main" id="main">
            <hgroup part="heading-group">
              <h1 part="title">${this.heading}</h1>
              <p part="summary">${this.summary}</p>
            </hgroup>
            <div part="content"><slot></slot></div>
          </main>
          <footer part="footer">
            <p part="footer-note"><small>Open source software from Livepeer.</small></p>
          </footer>
        </div>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "och-app-shell": OchAppShell
  }
}
