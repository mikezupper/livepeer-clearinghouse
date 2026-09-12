import {
  CursorPaginationController,
  formatAmount,
  readDisplayDenomination,
  subscribeDisplayDenomination,
  type DisplayDenomination
} from "@livepeer/clearinghouse-ui"
import { Effect } from "effect"
import { LitElement, css, html, nothing, svg, type TemplateResult } from "lit"
import { customElement, state } from "lit/decorators.js"
import { AdminApi, ApiFailure, forkAdmin, type Failure } from "./api.js"
import type { Overview, User, Workload } from "./domain.js"
import { metadata, routeFromPath, routes, type AdminIcon, type AdminRoute } from "./routes.js"

type Auth = "checking" | "out" | "denied" | "in"
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
  @state() private message = ""
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
    if (failure instanceof ApiFailure && failure.status === 401) this.auth = "out"
    else if (failure instanceof ApiFailure && failure.status === 403) this.auth = "denied"
    else if (this.auth === "checking") this.auth = "out"
    this.message = "Administration data is unavailable."
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
      if (!session.is_admin) return yield* Effect.fail(new ApiFailure({ status: 403 }))
      return yield* api.overview()
    }), (value) => { this.auth = "in"; this.data = value; this.loadCollection() })
  }
  private loadCollection(): void {
    if (this.auth !== "in") return
    const route = this.route
    const load = ++this.collectionLoad
    const cursor = this.pagination.cursor
    if (route !== "users" && route !== "workloads") return
    this.run(Effect.gen(function* () {
      const api = yield* AdminApi
      return route === "users"
        ? { tag: "users" as const, value: yield* api.users(cursor) }
        : { tag: "workloads" as const, value: yield* api.workloads(cursor) }
    }), (result) => {
      if (result.tag === "users") this.usersPage = result.value.items
      else this.workloadsPage = result.value.items
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
      this.message = verify ? "Signed in." : "A one-time code has been sent."
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
      this.run(Effect.flatMap(AdminApi, (api) => api.logout()), () => { this.auth = "out" }, () => { this.busy = false })
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
      this.message = value.enabled ? "Signer authorization stopped." : "Signer authorization resumed."
    }, () => { this.busy = false })
  }
  private signIn(): TemplateResult {
    return html`<section part="page" aria-labelledby="sign-in"><header part="page-header"><div><p part="eyebrow">Restricted operations</p><h2 id="sign-in">Sign in to administer the clearinghouse</h2></div></header><div part="card-grid"><article part="surface"><h3>Email a one-time code</h3><form @submit=${this.requestCode}><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" required><button ?disabled=${this.busy}>Email a one-time code</button></form></article>${this.codeRequested ? html`<article part="surface"><h3>Verify administrator code</h3><form @submit=${this.verifyCode}><label for="verify-email">Email address</label><input id="verify-email" name="email" type="email" autocomplete="email" required><label for="code">One-time code</label><input id="code" name="code" inputmode="numeric" .autocomplete=${"one-time-code"} pattern="[0-9]{6}" required><button ?disabled=${this.busy}>Verify code</button></form></article>` : nothing}</div>${this.providers.length > 1 ? html`<nav part="surface" aria-label="Other sign-in options"><h3>Other sign-in options</h3><ul>${this.providers.filter((provider) => provider !== "email").map((provider) => html`<li><a href=${`/v1/auth/oauth/${provider}/start`}>Continue with ${provider}</a></li>`)}</ul></nav>` : nothing}</section>`
  }
  private overview(data: Overview): TemplateResult {
    return html`<section part="page" aria-labelledby="overview"><header part="page-header"><div><p part="eyebrow">Core operations</p><h2 id="overview">Clearinghouse overview</h2></div><strong part=${data.global_stop.enabled ? "badge badge-danger" : "badge badge-success"}>${data.global_stop.enabled ? "Stopped" : "Authorizing"}</strong></header><dl part="metrics"><div part="surface"><dt>Users</dt><dd>${data.users}</dd></div><div part="surface"><dt>Active workloads</dt><dd>${data.active_workloads}</dd></div><div part="surface"><dt>Usage events</dt><dd>${data.usage}</dd></div><div part="surface"><dt>Signer-reported cost</dt><dd>${this.money(data.computed_fee, data.currency)}</dd></div></dl></section>`
  }
  private users(): TemplateResult {
    return html`<section part="page" aria-labelledby="users"><header part="page-header"><div><p part="eyebrow">Direct access</p><h2 id="users">Users and accounts</h2></div></header><div part="surface"><div part="table-scroll" tabindex="0" role="region" aria-label="Users and accounts"><table><caption>Authenticated users and personal accounts</caption><thead><tr><th scope="col">Email</th><th scope="col">User</th><th scope="col">Account</th><th scope="col">Access</th></tr></thead><tbody>${this.usersPage.map((user) => html`<tr><th scope="row">${user.email}</th><td><code>${user.user_id}</code></td><td><code>${user.account_id}</code></td><td>${user.is_admin ? "Administrator" : "User"}</td></tr>`)}</tbody></table></div></div>${this.paginationControl()}</section>`
  }
  private workloads(): TemplateResult {
    return html`<section part="page" aria-labelledby="workloads"><header part="page-header"><div><p part="eyebrow">Signer access</p><h2 id="workloads">Workloads</h2></div></header><div part="card-grid">${this.workloadsPage.map((workload) => html`<article part="surface"><h3>${workload.capability}</h3><p><code>${workload.id}</code></p><p>Account <code>${workload.account_id}</code></p><p>Access ends <time datetime=${workload.expires_at}>${date(workload.expires_at)}</time></p><strong part=${workload.status === "active" ? "badge badge-success" : "badge"}>${statusLabel(workload.status)}</strong></article>`)}</div>${this.paginationControl()}</section>`
  }
  private usage(data: Overview): TemplateResult {
    return html`<section part="page" aria-labelledby="usage"><header part="page-header"><div><p part="eyebrow">Metering health</p><h2 id="usage">Usage attribution</h2></div></header><dl part="metrics"><div part="surface"><dt>Total events</dt><dd>${data.usage}</dd></div><div part="surface"><dt>Unmatched events</dt><dd>${data.unmatched_usage}</dd></div><div part="surface"><dt>Computed fee</dt><dd>${this.money(data.computed_fee, data.currency)}</dd></div></dl></section>`
  }
  private operations(data: Overview): TemplateResult {
    return html`<section part="page" aria-labelledby="operations"><header part="page-header"><div><p part="eyebrow">Emergency control</p><h2 id="operations">Operations</h2></div></header><article part="surface"><h3>Global signer authorization</h3><p>${data.global_stop.reason}</p><p>Changed <time datetime=${data.global_stop.changed_at}>${new Date(data.global_stop.changed_at).toLocaleString()}</time></p><form @submit=${this.stop}><input type="hidden" name="enabled" value=${data.global_stop.enabled ? "false" : "true"}><label for="reason">Audit reason</label><input id="reason" name="reason" required><button ?disabled=${this.busy}>${data.global_stop.enabled ? "Resume authorization" : "Stop authorization"}</button></form></article></section>`
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
      ${this.message ? html`<p part="notice" role="status">${this.message}</p>` : nothing}
      ${this.auth === "checking" ? html`<p role="status">Checking administrator access…</p>` : nothing}
      ${this.auth === "out" ? this.signIn() : nothing}
      ${this.auth === "denied" ? html`<section part="page"><article part="surface"><h2>Administrator access required</h2><p>This signed-in account is not the configured administrator.</p></article></section>` : nothing}
      ${this.auth === "in" && this.data ? this.content(this.data) : nothing}
    </och-app-shell>`
  }
}
declare global { interface HTMLElementTagNameMap { "admin-app": AdminApp } }
