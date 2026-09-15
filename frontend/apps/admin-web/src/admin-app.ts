import {
  CursorPaginationController,
  failureMessage,
  formatAmount,
  readDisplayDenomination,
  subscribeDisplayDenomination,
  type DisplayDenomination
} from "@livepeer/clearinghouse-ui"
import { Effect } from "effect"
import { LitElement, css, html, nothing, svg, type TemplateResult } from "lit"
import { customElement, state } from "lit/decorators.js"
import { AdminApi, ApiFailure, InvalidPayload, NetworkFailure, forkAdmin, type Failure } from "./api.js"
import type { Overview, User, Workload } from "./domain.js"
import { metadata, routeFromPath, routes, type AdminIcon, type AdminRoute } from "./routes.js"

type Auth = "checking" | "out" | "denied" | "in"
type CollectionState = "loading" | "loaded"
const date = (value: string): string => new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium", timeStyle: "short"
}).format(new Date(value))
const statusLabel = (status: Workload["status"]): string => status.charAt(0).toUpperCase() + status.slice(1)
const icon = (name: AdminIcon): TemplateResult => {
  const path = name === "pulse" ? svg`<path d="M3 13h4l2.5-7 5 12 2.5-6h4"/>`
    : name === "users" ? svg`<circle cx="9" cy="8" r="3"/><path d="M3.5 19a5.5 5.5 0 0111 0m2-10a3 3 0 010 6"/>`
      : name === "workload" ? svg`<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9h10M7 13h6"/>`
        : name === "activity" ? svg`<path d="M4 20V10M9 20V4M14 20v-7M19 20V7"/>`
          : svg`<path d="M12 3l7 3v5c0 4.5-2.8 8-7 10-4.2-2-7-5.5-7-10V6zM9 9l6 6m0-6-6 6"/>`
  return svg`<svg part="navigation-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${path}</svg>`
}

@customElement("admin-app")
export class AdminApp extends LitElement {
  static styles = css`
    *, *::before, *::after { box-sizing: border-box; }
    :host, [part~="page"], [part~="surface"], form, dl { display: grid; gap: var(--och-space-2); }
    [part~="page-header"], [part~="actions"] { display: flex; align-items: center; justify-content: space-between; gap: var(--och-space-2); }
    [part~="metrics"], [part~="card-grid"] { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr)); gap: var(--och-space-2); }
    [part~="table-scroll"] { overflow-inline: auto; }
    table { inline-size: 100%; border-collapse: collapse; }
    th, td { padding: var(--och-space-1); text-align: start; border-block-end: var(--och-border-width) solid var(--och-color-border); }
    input, button { min-block-size: var(--och-control-min-block-size); padding: var(--och-space-1) var(--och-space-2); color: var(--och-color-text); background: var(--och-color-surface-raised); border: var(--och-border-width) solid var(--och-color-border-strong); border-radius: var(--och-radius-small); }
    code { overflow-wrap: anywhere; }
  `
  @state() private auth: Auth = "checking"
  @state() private route: AdminRoute = routeFromPath(location.pathname)
  @state() private data: Overview | undefined
  @state() private usersPage: ReadonlyArray<User> = []
  @state() private workloadsPage: ReadonlyArray<Workload> = []
  @state() private usersState: CollectionState = "loading"
  @state() private workloadsState: CollectionState = "loading"
  @state() private message = ""
  @state() private messageError = false
  @state() private codeRequested = false
  @state() private providers: ReadonlyArray<"email" | "google" | "github"> = ["email"]
  @state() private busy = false
  @state() private denomination: DisplayDenomination = "wei"
  private unsubscribeDenomination: (() => void) | undefined
  private readonly pagination = new CursorPaginationController(this)
  private collectionLoad = 0

  connectedCallback(): void {
    super.connectedCallback?.()
    addEventListener("popstate", this.onPopState)
    this.denomination = readDisplayDenomination()
    this.unsubscribeDenomination = subscribeDisplayDenomination((value) => { this.denomination = value })
    this.loadProviders()
    this.refresh()
  }
  disconnectedCallback(): void {
    removeEventListener("popstate", this.onPopState)
    this.unsubscribeDenomination?.()
    this.unsubscribeDenomination = undefined
    super.disconnectedCallback?.()
  }
  private readonly onPopState = (): void => {
    this.route = routeFromPath(location.pathname)
    this.pagination.restore()
    this.loadCollection()
  }
  private run<A>(effect: Effect.Effect<A, Failure, AdminApi>, success: (value: A) => void, complete: () => void = () => undefined, active: () => boolean = () => true): void {
    forkAdmin(effect, (value) => { if (this.isConnected && active()) success(value); complete() }, (failure) => { if (this.isConnected && active()) this.fail(failure); complete() })
  }
  private fail(failure: Failure): void {
    if (failure instanceof ApiFailure && failure.status === 401) {
      this.auth = "out"
      this.message = ""
      return
    }
    if (failure instanceof ApiFailure && failure.status === 403) {
      this.auth = "denied"
      this.message = ""
      return
    }
    if (this.auth === "checking") this.auth = "out"
    const reason = failure instanceof InvalidPayload ? "invalid-response"
      : failure instanceof NetworkFailure || failure.status >= 500 ? "unavailable"
        : failure.status === 409 ? "conflict" : "rejected"
    this.messageError = true
    this.message = failureMessage(failure.operation, reason)
  }
  private loadProviders(): void {
    forkAdmin(Effect.flatMap(AdminApi, (api) => api.providers()), (value) => {
      if (this.isConnected) this.providers = value.providers
    }, () => undefined)
  }
  private refresh(): void {
    this.run(Effect.gen(function* () {
      const api = yield* AdminApi
      const session = yield* api.session()
      if (!session.is_admin) return yield* Effect.fail(new ApiFailure({ status: 403, operation: "verify administrator role" }))
      return yield* api.overview()
    }), (value) => { this.auth = "in"; this.data = value; this.loadCollection() })
  }
  private loadCollection(): void {
    if (this.auth !== "in") return
    const route = this.route
    const load = ++this.collectionLoad
    const cursor = this.pagination.cursor
    if (route !== "users" && route !== "workloads") return
    if (route === "users") this.usersState = "loading"
    else this.workloadsState = "loading"
    this.run(Effect.gen(function* () {
      const api = yield* AdminApi
      return route === "users"
        ? { tag: "users" as const, value: yield* api.users(cursor) }
        : { tag: "workloads" as const, value: yield* api.workloads(cursor) }
    }), (result) => {
      if (result.tag === "users") {
        this.usersPage = result.value.items
        this.usersState = "loaded"
      } else {
        this.workloadsPage = result.value.items
        this.workloadsState = "loaded"
      }
      this.pagination.received(result.value.next_cursor)
    }, () => undefined, () => load === this.collectionLoad)
  }
  private submitAuth(event: SubmitEvent, verify: boolean): void {
    event.preventDefault()
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    if (this.busy) return
    this.busy = true
    const form = event.currentTarget
    const values = new FormData(form)
    this.run(Effect.flatMap(AdminApi, (api) => verify
      ? api.verifyCode(String(values.get("email") ?? ""), String(values.get("code") ?? ""))
      : api.requestCode(String(values.get("email") ?? ""))), () => {
      if (verify) this.refresh()
      else this.codeRequested = true
      this.message = verify ? "Administrator session started." : "Administrator sign-in code sent."
      this.messageError = false
    }, () => { this.busy = false })
  }
  private requestCode(event: SubmitEvent): void { this.submitAuth(event, false) }
  private verifyCode(event: SubmitEvent): void { this.submitAuth(event, true) }
  private money(numerator: string, currency: string): TemplateResult {
    const amount = formatAmount(numerator, 1n, currency, this.denomination)
    return html`<data value=${numerator}>${amount.primary}</data>${amount.exactWei ? html` <small>(${amount.exactWei} exact)</small>` : nothing}`
  }
  private navigate(event: MouseEvent): void {
    if (!(event.currentTarget instanceof HTMLAnchorElement)) return
    event.preventDefault()
    history.pushState(null, "", event.currentTarget.href)
    this.pagination.reset()
    this.onPopState()
  }
  private changePage(event: CustomEvent<{ readonly direction: "next" | "previous" }>): void {
    if (this.pagination.move(event.detail.direction)) this.loadCollection()
  }
  private paginationControl(): TemplateResult {
    return html`<och-cursor-pagination
      .hasPrevious=${this.pagination.hasPrevious}
      .hasNext=${this.pagination.hasNext}
      .busy=${this.busy}
      .page=${this.pagination.pageNumber}
      @cursor-page-change=${this.changePage}
      exportparts="navigation: pagination, status: pagination-status, actions: pagination-actions, button: pagination-button, previous: pagination-previous, next: pagination-next"
    ></och-cursor-pagination>`
  }
  private action(event: MouseEvent): void {
    if (!(event.target instanceof HTMLButtonElement)) return
    if (event.target.dataset.action === "logout" && !this.busy) {
      this.busy = true
      this.run(Effect.flatMap(AdminApi, (api) => api.logout()), () => {
        this.auth = "out"
        this.message = "Administrator session ended."
        this.messageError = false
      }, () => { this.busy = false })
    }
  }
  private stop(event: SubmitEvent): void {
    event.preventDefault()
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    if (this.busy) return
    this.busy = true
    const values = new FormData(event.currentTarget)
    const enabled = values.get("enabled") === "true"
    this.run(Effect.flatMap(AdminApi, (api) => api.setStop(enabled, String(values.get("reason") ?? ""))), (value) => {
      if (this.data) this.data = { ...this.data, global_stop: value }
      this.message = value.enabled ? "Signer authorization paused." : "Signer authorization resumed."
      this.messageError = false
    }, () => { this.busy = false })
  }
  private signIn(): TemplateResult {
    return html`
      <section part="page" aria-labelledby="sign-in">
        <header part="page-header">
          <div>
            <p part="eyebrow">Restricted operations</p>
            <h2 id="sign-in">Sign in to administer the clearinghouse</h2>
            <p>Use the configured administrator identity to inspect account access, workload authorization, usage attribution, and signer controls.</p>
          </div>
        </header>
        <div part="card-grid">
          <article part="surface">
            <h3>Request an administrator sign-in code</h3>
            <p>The one-time code is sent to the email address you enter and can start an administrator session after verification.</p>
            <form @submit=${this.requestCode}>
              <label for="email">Administrator email address</label>
              <input id="email" name="email" type="email" autocomplete="email" aria-describedby="email-hint" required>
              <p id="email-hint"><small>Enter the email address configured for clearinghouse administration.</small></p>
              <button ?disabled=${this.busy}>Send administrator sign-in code</button>
            </form>
          </article>
          ${this.codeRequested ? html`
            <article part="surface">
              <h3>Verify the administrator sign-in code</h3>
              <p>Enter the same email address and the six-digit code from the sign-in message.</p>
              <form @submit=${this.verifyCode}>
                <label for="verify-email">Administrator email address</label>
                <input id="verify-email" name="email" type="email" autocomplete="email" aria-describedby="verify-email-hint" required>
                <p id="verify-email-hint"><small>Use the address that received the one-time code.</small></p>
                <label for="code">Six-digit sign-in code</label>
                <input id="code" name="code" inputmode="numeric" .autocomplete=${"one-time-code"} pattern="[0-9]{6}" aria-describedby="code-hint" required>
                <p id="code-hint"><small>The code can be used once. Request a new code if it has expired.</small></p>
                <button ?disabled=${this.busy}>Sign in as administrator</button>
              </form>
            </article>
          ` : nothing}
        </div>
        ${this.providers.length > 1 ? html`
          <nav part="surface" aria-label="Other administrator sign-in options">
            <h3>Use a connected identity provider</h3>
            <p>A connected provider must resolve to the configured administrator identity.</p>
            <ul>${this.providers.filter((provider) => provider !== "email").map((provider) => html`
              <li><a href=${`/v1/auth/oauth/${provider}/start`}>Sign in with ${provider === "github" ? "GitHub" : "Google"}</a></li>
            `)}</ul>
          </nav>
        ` : nothing}
      </section>
    `
  }
  private overview(data: Overview): TemplateResult {
    return html`
      <section part="page" aria-labelledby="overview">
        <header part="page-header">
          <div>
            <p part="eyebrow">Core operations</p>
            <h2 id="overview">Clearinghouse overview</h2>
            <p>Review current access, workload, and metering totals before investigating records or changing signer authorization.</p>
          </div>
          <strong part=${data.global_stop.enabled ? "badge badge-danger" : "badge badge-success"}>${data.global_stop.enabled ? "Authorization paused" : "Authorization available"}</strong>
        </header>
        <section part="surface" aria-labelledby="overview-activity">
          <h3 id="overview-activity">Current clearinghouse activity</h3>
          <p>These totals summarize all recorded users, active signer access, and received usage events.</p>
          <dl part="metrics">
            <div part="surface"><dt>Users</dt><dd>${data.users}</dd></div>
            <div part="surface"><dt>Active workloads</dt><dd>${data.active_workloads}</dd></div>
            <div part="surface"><dt>Usage events</dt><dd>${data.usage}</dd></div>
            <div part="surface"><dt>Signer-reported cost <och-help-tip term="signer-reported cost">The fee reported by signer metering for attributed usage events.</och-help-tip></dt><dd>${this.money(data.computed_fee, data.currency)}</dd></div>
          </dl>
        </section>
      </section>
    `
  }
  private users(): TemplateResult {
    return html`
      <section part="page" aria-labelledby="users">
        <header part="page-header">
          <div>
            <p part="eyebrow">Account access</p>
            <h2 id="users">Users and accounts</h2>
            <p>Trace each signed-in user to the personal account that owns their credentials, workloads, and usage.</p>
          </div>
        </header>
        <section part="surface" aria-labelledby="access-directory">
          <h3 id="access-directory">Authenticated access directory</h3>
          <p>Use these identifiers when investigating which personal account authorized a workload or received attributed usage.</p>
          ${this.usersState === "loading" ? html`
            <p role="status">Loading users and personal accounts…</p>
          ` : this.usersPage.length === 0 ? html`
            <p>No users or personal accounts have been recorded. A personal account appears after a user signs in for the first time.</p>
          ` : html`
            <div part="table-scroll" tabindex="0" role="region" aria-label="Authenticated users and personal accounts">
              <table>
                <caption>Signed-in users and the personal accounts they own</caption>
                <thead><tr><th scope="col">Email address</th><th scope="col">User ID</th><th scope="col">Account ID</th><th scope="col">Access level</th></tr></thead>
                <tbody>${this.usersPage.map((user) => html`<tr><th scope="row">${user.email}</th><td><code>${user.user_id}</code></td><td><code>${user.account_id}</code></td><td>${user.is_admin ? "Administrator" : "User"}</td></tr>`)}</tbody>
              </table>
            </div>
          `}
        </section>
        ${this.paginationControl()}
      </section>
    `
  }
  private workloads(): TemplateResult {
    return html`
      <section part="page" aria-labelledby="workloads">
        <header part="page-header">
          <div>
            <p part="eyebrow">Quoted signer access</p>
            <h2 id="workloads">Workloads</h2>
            <p>Inspect time-bounded authorizations created from network offers and trace each one to its owning account.</p>
          </div>
        </header>
        <section aria-labelledby="workload-records">
          <h3 id="workload-records">Quoted authorization records</h3>
          <p>A workload status describes signer access, not whether the runner completed the user's job.</p>
          ${this.workloadsState === "loading" ? html`
            <p role="status">Loading workload authorization records…</p>
          ` : this.workloadsPage.length === 0 ? html`
            <article part="surface">
              <h4>No workloads recorded</h4>
              <p>A workload appears after a user creates quoted access from a network offer.</p>
            </article>
          ` : html`
            <div part="card-grid">${this.workloadsPage.map((workload) => html`
              <article part="surface">
                <h4>${workload.capability}</h4>
                <p>Workload ID <code>${workload.id}</code></p>
                <p>Owning account <code>${workload.account_id}</code></p>
                <p>${workload.max_spend_wei === null ? "No spend ceiling" : html`Maximum spend ${this.money(workload.max_spend_wei, "wei")}`}</p>
                <p>Access expires <time datetime=${workload.expires_at}>${date(workload.expires_at)}</time></p>
                <strong part=${workload.status === "active" ? "badge badge-success" : "badge"}>${statusLabel(workload.status)}</strong>
              </article>
            `)}</div>
          `}
        </section>
        ${this.paginationControl()}
      </section>
    `
  }
  private usage(data: Overview): TemplateResult {
    return html`
      <section part="page" aria-labelledby="usage">
        <header part="page-header">
          <div>
            <p part="eyebrow">Metering health</p>
            <h2 id="usage">Usage attribution</h2>
            <p>Compare signer usage with workload attribution before relying on account-level cost records.</p>
          </div>
        </header>
        <section part="surface" aria-labelledby="usage-summary">
          <h3 id="usage-summary">Recorded metering outcomes</h3>
          <p>Unmatched usage events are retained for investigation but are not attributed to another account.</p>
          <dl part="metrics">
            <div part="surface"><dt>Usage events</dt><dd>${data.usage}</dd></div>
            <div part="surface"><dt>Unmatched usage events <och-help-tip term="unmatched usage events">Signer events that could not be correlated to a workload.</och-help-tip></dt><dd>${data.unmatched_usage}</dd></div>
            <div part="surface"><dt>Signer-reported cost <och-help-tip term="signer-reported cost">The fee reported by signer metering for attributed usage events.</och-help-tip></dt><dd>${this.money(data.computed_fee, data.currency)}</dd></div>
          </dl>
        </section>
      </section>
    `
  }
  private operations(data: Overview): TemplateResult {
    return html`
      <section part="page" aria-labelledby="operations">
        <header part="page-header">
          <div>
            <p part="eyebrow">Emergency control</p>
            <h2 id="operations">Operations</h2>
            <p>Pause or resume authorization for every signer request. Pausing fails new authorization closed across all accounts and workloads.</p>
          </div>
        </header>
        <article part="surface" aria-labelledby="signer-authorization">
          <h3 id="signer-authorization">Global signer authorization</h3>
          <p><strong>Current state:</strong> ${data.global_stop.enabled ? "Authorization paused" : "Authorization available"}</p>
          <p><strong>Recorded reason:</strong> ${data.global_stop.reason}</p>
          <p>State last changed <time datetime=${data.global_stop.changed_at}>${date(data.global_stop.changed_at)}</time></p>
          <form @submit=${this.stop}>
            <input type="hidden" name="enabled" value=${data.global_stop.enabled ? "false" : "true"}>
            <label for="reason">Reason for ${data.global_stop.enabled ? "resuming" : "pausing"} signer authorization</label>
            <input id="reason" name="reason" aria-describedby="reason-hint" required>
            <p id="reason-hint"><small>This reason is stored with the authorization state so operators can understand why it changed.</small></p>
            <button ?disabled=${this.busy}>${data.global_stop.enabled ? "Resume signer authorization" : "Pause signer authorization"}</button>
          </form>
        </article>
      </section>
    `
  }
  private content(data: Overview): TemplateResult {
    if (this.route === "users") return this.users()
    if (this.route === "workloads") return this.workloads()
    if (this.route === "usage") return this.usage(data)
    if (this.route === "operations") return this.operations(data)
    return this.overview(data)
  }
  protected render(): TemplateResult {
    const route = metadata(this.route)
    return html`<och-app-shell @click=${this.action} .heading=${this.auth === "in" ? route.label : "Clearinghouse administration"} .summary=${this.auth === "in" ? route.summary : "Operate the Livepeer clearinghouse core."} exportparts="skip-link, shell, sidebar, header, sidebar-header, brand, brand-name, brand-accent, brand-subtitle, sidebar-close-button, sidebar-close-icon, sidebar-close-label, navigation, navigation-backdrop, workspace, topbar, menu-button, menu-icon, menu-label, mobile-brand, context, utility, main, heading-group, title, summary, content, footer, footer-note">
      ${this.auth === "in" ? html`<p slot="navigation" part="navigation-heading">Administration</p><ul slot="navigation" part="navigation-list">${routes.map((item) => html`<li><a part=${this.route === item.route ? "navigation-link navigation-link-active" : "navigation-link"} href=${item.href} aria-current=${this.route === item.route ? "page" : "false"} @click=${this.navigate}>${icon(item.icon)}<span part="navigation-label">${item.label}</span></a></li>`)}</ul><p slot="utility"><button data-action="logout" ?disabled=${this.busy}>Sign out</button></p>` : nothing}
      <och-denomination-control slot="utility" exportparts="field: denomination-field, label: denomination-label, select: denomination-select"></och-denomination-control>
      <p slot="context">${this.auth === "in" ? "Administrator" : "Restricted console"}</p>
      ${this.message ? html`<p part=${this.messageError ? "notice notice-danger" : "notice"} role=${this.messageError ? "alert" : "status"}>${this.message}</p>` : nothing}
      ${this.auth === "checking" ? html`<p role="status">Checking whether this session has administrator access…</p>` : nothing}
      ${this.auth === "out" ? this.signIn() : nothing}
      ${this.auth === "denied" ? html`<section part="page"><article part="surface"><h2>Administrator access required</h2><p>This signed-in user is not the configured administrator. Sign out, then sign in with the configured administrator email address.</p><button data-action="logout" ?disabled=${this.busy}>Sign out and use another account</button></article></section>` : nothing}
      ${this.auth === "in" && this.data ? this.content(this.data) : nothing}
    </och-app-shell>`
  }
}
declare global { interface HTMLElementTagNameMap { "admin-app": AdminApp } }
