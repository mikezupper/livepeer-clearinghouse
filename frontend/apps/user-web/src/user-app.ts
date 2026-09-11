import "@livepeer/clearinghouse-ui"
import { msg } from "@lit/localize"
import { DateTime, Effect } from "effect"
import { LitElement, css, html, nothing, svg, type TemplateResult } from "lit"
import { customElement, state } from "lit/decorators.js"
import { ApiFailure, runUser, UserApi, type UserFailure } from "./api.js"
import type { Account, AuthSession, Balance, CatalogEntry, Charge, Credential, NewSignerSession, Provider, SignerSessionMetadata, Usage } from "./contracts.js"
import { userNavigation, userRouteFromPathname, userRouteMetadata, type UserNavigationIcon, type UserRoute } from "./navigation.js"

type AuthState =
  | { readonly tag: "checking" }
  | { readonly tag: "signedOut" }
  | { readonly tag: "denied" }
  | { readonly tag: "unavailable" }
  | { readonly tag: "unscoped"; readonly session: AuthSession }
  | { readonly tag: "scoped"; readonly session: AuthSession }
type Notice = { readonly tone: "status" | "error"; readonly text: string }
type Secret = { readonly heading: string; readonly value: string; readonly instructions: string }
type PendingCreate = { readonly fingerprint: string; readonly key: string }

const instantDate = (value: unknown): Date => DateTime.isDateTime(value) ? DateTime.toDate(value) : new Date(String(value))
const instantAttribute = (value: unknown): string => DateTime.isDateTime(value) ? DateTime.formatIso(value) : String(value)
const formatInstant = (value: unknown): string => new Intl.DateTimeFormat(document.documentElement.lang || "en", {
  dateStyle: "medium", timeStyle: "short"
}).format(instantDate(value))
const renderProvider = (provider: Provider): TemplateResult | typeof nothing => provider === "email"
  ? nothing
  : html`<li><a href=${`/v1/auth/oauth/${provider}/start`}>Continue with ${provider === "google" ? "Google" : "GitHub"}</a></li>`
const renderCredential = (item: Credential): TemplateResult => html`
  <tr><th scope="row">${item.label}</th><td><code>${item.prefix}…</code></td><td><strong part=${item.status === "active" ? "badge badge-success" : "badge badge-muted"} data-tone=${item.status === "active" ? "success" : "muted"}>${item.status}</strong></td>
    <td><time datetime=${instantAttribute(item.created_at)}>${formatInstant(item.created_at)}</time></td><td>
      <button type="button" data-action="rotate-credential" data-id=${item.id.toString()} ?disabled=${item.status !== "active"}>Rotate</button>
      <button type="button" data-action="revoke-credential" data-id=${item.id.toString()} ?disabled=${item.status !== "active"}>Revoke</button>
    </td></tr>`
const renderSession = (item: SignerSessionMetadata): TemplateResult => html`
  <article part="surface session-card"><header part="card-header"><div><p part="eyebrow">Signer session</p><h3><code>${item.id}</code></h3></div><strong part="badge badge-success" data-tone="success">Active</strong></header><dl part="compact-metrics">
    <div><dt>Available</dt><dd>${item.lease.available} ${item.lease.unit}</dd></div>
    <div><dt>Pending</dt><dd>${item.lease.pending} ${item.lease.unit}</dd></div>
    <div><dt>Expires</dt><dd><time datetime=${instantAttribute(item.expires_at)}>${formatInstant(item.expires_at)}</time></dd></div>
  </dl><p part="actions"><button type="button" data-action="refresh-session" data-id=${item.id.toString()}>Refresh</button>
  <button type="button" data-action="revoke-session" data-id=${item.id.toString()}>Revoke</button></p></article>`
const renderCatalogEntry = (item: CatalogEntry): TemplateResult => html`
  <tr><th scope="row">${item.capability}</th><td>${item.model ?? "Any"}</td>
  <td>${item.rate.numerator} / ${item.rate.denominator} ${item.rate.charge_unit} per ${item.rate.quantity_unit}</td>
  <td><strong part=${item.available ? "badge badge-success" : "badge badge-warning"} data-tone=${item.available ? "success" : "warning"}>${item.available ? "Available" : "Unavailable"}</strong></td></tr>`
const renderUsage = (item: Usage): TemplateResult => html`
  <tr><th scope="row"><time datetime=${item.occurred_at}>${formatInstant(item.occurred_at)}</time></th>
  <td>${item.capability}${item.model === undefined ? nothing : ` — ${item.model}`}</td>
  <td>${item.quantity.value} ${item.quantity.unit}</td><td><code>${item.event_id}</code></td></tr>`
const renderCharge = (item: Charge): TemplateResult => html`
  <tr><th scope="row"><time datetime=${instantAttribute(item.created_at)}>${formatInstant(item.created_at)}</time></th>
  <td>${item.amount.value} ${item.amount.unit}</td><td><code>${item.usage_event_id}</code></td></tr>`
const renderNavigationIcon = (icon: UserNavigationIcon): TemplateResult => {
  const drawing = (() => {
    switch (icon) {
      case "wallet": return svg`<path d="M3 7.5h15a3 3 0 0 1 3 3v7.5H6a3 3 0 0 1-3-3z"></path><path d="M3 7.5 15 4v3.5"></path><path d="M16 12h5"></path>`
      case "key": return svg`<circle cx="8" cy="12" r="4"></circle><path d="m12 12 9-9M17 7l2 2M14 10l2 2"></path>`
      case "linked-clock": return svg`<circle cx="9" cy="12" r="6"></circle><path d="M9 9v3l2 2M15 8h3a3 3 0 0 1 0 6h-2M3 16H2a2 2 0 0 0 0 4h4"></path>`
      case "catalog-grid": return svg`<rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1"></rect><rect x="3" y="14" width="7" height="7" rx="1"></rect><path d="M14 17.5h7M17.5 14v7"></path>`
      case "activity-bars": return svg`<path d="M4 20V10M9 20V4M14 20v-7M19 20V7"></path>`
      case "receipt": return svg`<path d="M5 3h14v18l-3-2-4 2-4-2-3 2z"></path><path d="M8 8h8M8 12h8M8 16h5"></path>`
      case "user-shield": return svg`<circle cx="9" cy="8" r="3"></circle><path d="M3.5 18a5.5 5.5 0 0 1 9.5-3.8M17 12l4 1.7v3.1c0 2.3-1.7 4.3-4 4.9-2.3-.6-4-2.6-4-4.9v-3.1z"></path>`
    }
  })()
  return svg`<svg part="navigation-icon" data-icon=${icon} viewBox="0 0 24 24" aria-hidden="true" focusable="false">${drawing}</svg>`
}

@customElement("user-app")
export class UserApp extends LitElement {
  static styles = css`
    *, *::before, *::after { box-sizing: border-box; }
    :host { display: block; }
    [part~="page"], [part~="surface"], [part~="form-card"], form, fieldset, dl { display: grid; gap: var(--och-user-content-gap); }
    [part~="page-header"], [part~="card-header"], [part~="actions"] { display: flex; align-items: center; justify-content: space-between; gap: var(--och-user-content-gap); }
    [part~="metrics"], [part~="card-grid"], [part~="auth-grid"] { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--och-user-card-min-size)), 1fr)); gap: var(--och-user-content-gap); }
    [part~="compact-metrics"] { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    [part~="table-scroll"] { overflow-inline: auto; }
    table { inline-size: 100%; border-collapse: collapse; }
    th, td { padding: var(--och-user-table-cell-padding); text-align: start; border-block-end: var(--och-border-width) solid var(--och-color-border); }
    input, button { min-block-size: var(--och-control-min-block-size); padding: var(--och-user-control-padding); color: var(--och-color-text); background: var(--och-user-control-background); border: var(--och-border-width) solid var(--och-user-control-border); border-radius: var(--och-radius-small); }
    button { cursor: pointer; font-weight: var(--och-user-control-weight); }
    button:hover { border-color: var(--och-color-border-strong); }
    button:disabled { cursor: not-allowed; opacity: var(--och-user-disabled-opacity); }
    fieldset { padding: 0; border: 0; }
    caption { padding-block: var(--och-user-table-cell-padding); text-align: start; }
    [part~="metric"] dt, [part~="identity-details"] dt { color: var(--och-color-text-muted); font-size: var(--och-font-size-small); }
    [part~="metric"] dd { color: var(--och-color-text-strong); font-size: var(--och-font-size-1); font-weight: var(--och-user-metric-weight); }
    [part~="badge"] { display: inline-flex; align-items: center; inline-size: fit-content; }
    dialog { inline-size: min(var(--och-user-dialog-max-size), calc(100vi - 2 * var(--och-space-3))); }
    code { overflow-wrap: anywhere; }
  `
  @state() private auth: AuthState = { tag: "checking" }
  @state() private providers: readonly Provider[] = []
  @state() private route: UserRoute = userRouteFromPathname(window.location.pathname)
  @state() private notice?: Notice
  @state() private account?: Account
  @state() private balance?: Balance
  @state() private credentials: readonly Credential[] = []
  @state() private sessions: readonly SignerSessionMetadata[] = []
  @state() private catalog: readonly CatalogEntry[] = []
  @state() private usage: readonly Usage[] = []
  @state() private charges: readonly Charge[] = []
  @state() private usageCursor: string | null | undefined = undefined
  @state() private chargesCursor: string | null | undefined = undefined
  @state() private inFlight: ReadonlySet<string> = new Set()
  @state() private secret: Secret | undefined = undefined
  private pendingCreate: PendingCreate | undefined = undefined
  private refreshKeys: ReadonlyMap<string, string> = new Map()

  connectedCallback(): void {
    super.connectedCallback?.()
    window.addEventListener("popstate", this.onPopState)
    this.loadIdentity()
  }
  disconnectedCallback(): void {
    window.removeEventListener("popstate", this.onPopState)
    this.clearSecret()
    super.disconnectedCallback?.()
  }
  private readonly onPopState = (): void => {
    this.clearSecret()
    this.route = userRouteFromPathname(window.location.pathname)
    this.loadRoute()
  }
  private run<A>(
    effect: Effect.Effect<A, UserFailure, UserApi>,
    success: (value: A) => void,
    settled: () => void = () => undefined
  ): void {
    runUser(effect.pipe(Effect.either)).then((result) => {
      if (result._tag === "Left") this.fail(result.left)
      else success(result.right)
      settled()
    })
  }
  private runCommand<A>(
    command: string,
    effect: Effect.Effect<A, UserFailure, UserApi>,
    success: (value: A) => void
  ): void {
    if (this.inFlight.has(command)) return
    this.inFlight = new Set([...this.inFlight, command])
    this.run(effect, success, () => {
      this.inFlight = new Set([...this.inFlight].filter((item) => item !== command))
    })
  }
  private createKey(input: NewSignerSession): string {
    const fingerprint = JSON.stringify(input)
    if (this.pendingCreate?.fingerprint === fingerprint) return this.pendingCreate.key
    const key = crypto.randomUUID()
    this.pendingCreate = { fingerprint, key }
    return key
  }
  private refreshKey(id: string): string {
    const existing = this.refreshKeys.get(id)
    if (existing !== undefined) return existing
    const key = crypto.randomUUID()
    this.refreshKeys = new Map([...this.refreshKeys, [id, key]])
    return key
  }
  private clearRefreshKey(id: string): void {
    this.refreshKeys = new Map([...this.refreshKeys].filter(([sessionId]) => sessionId !== id))
  }
  private fail(failure: UserFailure): void {
    if (failure instanceof ApiFailure && failure.status === 401) this.auth = { tag: "signedOut" }
    this.notice = { tone: "error", text: failure instanceof ApiFailure && failure.status === 403
      ? "That action is not permitted for this account."
      : failure instanceof ApiFailure && failure.status === 429
        ? "Too many attempts. Wait before trying again."
        : "The clearinghouse could not complete that request. Try again." }
  }
  private loadIdentity(): void {
    this.run(Effect.gen(function* () {
      const api = yield* UserApi
      return { providers: yield* api.providers(), session: yield* api.session().pipe(Effect.either) }
    }), ({ providers, session }) => {
      this.providers = providers.providers
      this.auth = session._tag === "Left"
        ? session.left instanceof ApiFailure && session.left.status === 401
          ? { tag: "signedOut" }
          : session.left instanceof ApiFailure && session.left.status === 403
            ? { tag: "denied" }
            : { tag: "unavailable" }
        : session.right.account_id === undefined ? { tag: "unscoped", session: session.right }
          : { tag: "scoped", session: session.right }
      this.loadRoute()
    })
  }
  private loadRoute(): void {
    if (this.auth.tag !== "scoped") return
    const accountId = this.auth.session.account_id
    if (accountId === undefined) return
    const route = this.route
    if (route === "usage") {
      this.usage = []
      this.usageCursor = undefined
    }
    if (route === "charges") {
      this.charges = []
      this.chargesCursor = undefined
    }
    this.run(Effect.gen(function* () {
      const api = yield* UserApi
      switch (route) {
        case "overview": return { tag: "overview" as const, account: yield* api.account(accountId), balance: yield* api.balance(accountId) }
        case "credentials": return { tag: "credentials" as const, value: yield* api.credentials() }
        case "sessions": return { tag: "sessions" as const, value: (yield* api.signerSessions()).items }
        case "catalog": return { tag: "catalog" as const, value: yield* api.catalog() }
        case "usage": return { tag: "usage" as const, value: yield* api.usage(accountId) }
        case "charges": return { tag: "charges" as const, value: yield* api.charges(accountId) }
        case "profile": return { tag: "profile" as const }
      }
    }), (result) => {
      if (result.tag === "overview") { this.account = result.account; this.balance = result.balance }
      if (result.tag === "credentials") this.credentials = result.value
      if (result.tag === "sessions") this.sessions = result.value
      if (result.tag === "catalog") this.catalog = result.value
      if (result.tag === "usage") {
        this.usage = result.value.items
        this.usageCursor = result.value.page.next_cursor
      }
      if (result.tag === "charges") {
        this.charges = result.value.items
        this.chargesCursor = result.value.page.next_cursor
      }
    })
  }
  private navigate(event: Event): void {
    const anchor = event.currentTarget
    if (!(anchor instanceof HTMLAnchorElement)) return
    event.preventDefault()
    this.clearSecret()
    history.pushState({}, "", anchor.href)
    this.route = userRouteFromPathname(window.location.pathname)
    this.loadRoute()
  }
  private requestCode(event: SubmitEvent): void {
    event.preventDefault()
    const form = event.currentTarget
    if (!(form instanceof HTMLFormElement)) return
    const email = String(new FormData(form).get("email") ?? "")
    this.runCommand("request-code", Effect.flatMap(UserApi, (api) => api.requestCode(email)), () => {
      this.notice = { tone: "status", text: msg("If that address can sign in, a one-time code has been sent.") }
      const verifyEmail = this.renderRoot.querySelector<HTMLInputElement>("#verify-email")
      if (verifyEmail !== null) verifyEmail.value = email
    })
  }
  private verifyCode(event: SubmitEvent): void {
    event.preventDefault()
    const form = event.currentTarget
    if (!(form instanceof HTMLFormElement)) return
    const data = new FormData(form)
    this.runCommand("verify-code", Effect.flatMap(UserApi, (api) => api.verifyCode(String(data.get("email") ?? ""), String(data.get("code") ?? ""))), (session) => {
      form.reset()
      this.auth = session.account_id === undefined ? { tag: "unscoped", session } : { tag: "scoped", session }
      this.notice = { tone: "status", text: msg("Signed in.") }
      this.loadRoute()
    })
  }
  private redeem(event: SubmitEvent): void {
    event.preventDefault()
    const form = event.currentTarget
    if (!(form instanceof HTMLFormElement)) return
    const invitation = String(new FormData(form).get("invitation") ?? "")
    this.runCommand("redeem", Effect.flatMap(UserApi, (api) => api.redeemInvitation(invitation)), () => {
      form.reset()
      this.auth = { tag: "signedOut" }
      this.notice = { tone: "status", text: msg("Identity linked. Sign in again to continue.") }
    })
  }
  private issueCredential(event: SubmitEvent): void {
    event.preventDefault()
    if (this.auth.tag !== "scoped") return
    const form = event.currentTarget
    if (!(form instanceof HTMLFormElement)) return
    const label = String(new FormData(form).get("label") ?? "")
    const { account_id: accountId, principal_id: principalId } = this.auth.session
    if (accountId === undefined) return
    this.runCommand("issue-credential", Effect.flatMap(UserApi, (api) => api.issueCredential(accountId, principalId, label)), (issued) => {
      form.reset()
      this.credentials = [...this.credentials, issued.credential]
      this.showSecret("Credential created", issued.secret, "Save this credential now. It cannot be shown again.")
    })
  }
  private createSession(event: SubmitEvent): void {
    event.preventDefault()
    const form = event.currentTarget
    if (!(form instanceof HTMLFormElement)) return
    const data = new FormData(form)
    const model = String(data.get("model") ?? "").trim()
    const input: NewSignerSession = {
      capability: String(data.get("capability") ?? ""), ...(model === "" ? {} : { model }),
      app: String(data.get("app") ?? ""), requested_cap: String(data.get("cap") ?? ""),
      unit: "wei", ttl_seconds: Number(data.get("ttl") ?? 3600)
    }
    const idempotencyKey = this.createKey(input)
    this.runCommand("create-session", Effect.flatMap(UserApi, (api) => api.createSignerSession(input, idempotencyKey)), (session) => {
      this.pendingCreate = undefined
      form.reset()
      this.showSecret("Signer session created", session.token, `Use with ${session.signer_url}. This token cannot be shown again.`)
      this.loadRoute()
    })
  }
  private loadMore(kind: "usage" | "charges"): void {
    if (this.auth.tag !== "scoped" || this.auth.session.account_id === undefined) return
    const cursor = kind === "usage" ? this.usageCursor : this.chargesCursor
    if (typeof cursor !== "string") return
    const accountId = this.auth.session.account_id
    if (kind === "usage") {
      this.runCommand("load-more-usage", Effect.flatMap(UserApi, (api) => api.usage(accountId, cursor)), (page) => {
        this.usage = [...this.usage, ...page.items]
        this.usageCursor = page.page.next_cursor
      })
    } else {
      this.runCommand("load-more-charges", Effect.flatMap(UserApi, (api) => api.charges(accountId, cursor)), (page) => {
        this.charges = [...this.charges, ...page.items]
        this.chargesCursor = page.page.next_cursor
      })
    }
  }
  private handleAction(event: Event): void {
    const button = event.target
    if (!(button instanceof HTMLButtonElement)) return
    const action = button.dataset.action
    const id = button.dataset.id
    if (action === "close-secret") { this.clearSecret(); return }
    if (action === "logout") { this.logout(); return }
    if (action === "load-more-usage") { this.loadMore("usage"); return }
    if (action === "load-more-charges") { this.loadMore("charges"); return }
    if (action === "refresh-browser") {
      this.runCommand("refresh-browser", Effect.flatMap(UserApi, (api) => api.refreshBrowser()), (session) => {
        this.auth = session.account_id === undefined ? { tag: "unscoped", session } : { tag: "scoped", session }
        this.notice = { tone: "status", text: "Browser session refreshed." }
      })
      return
    }
    if (id === undefined) return
    if ((action === "rotate-credential" || action === "refresh-session" || action === "revoke-credential" || action === "revoke-session")
      && !window.confirm(msg("Continue? Existing access may stop immediately."))) return
    if (action === "rotate-credential") this.runCommand(`rotate-credential-${id}`, Effect.flatMap(UserApi, (api) => api.rotateCredential(id)), (issued) => {
      this.credentials = this.credentials.map((item) => item.id === id ? issued.credential : item)
      this.showSecret("Credential rotated", issued.secret, "Replace the old credential now. This secret cannot be shown again.")
    })
    if (action === "revoke-credential") this.runCommand(`revoke-credential-${id}`, Effect.flatMap(UserApi, (api) => api.revokeCredential(id)), () => this.loadRoute())
    if (action === "refresh-session") {
      const idempotencyKey = this.refreshKey(id)
      this.runCommand(`refresh-session-${id}`, Effect.flatMap(UserApi, (api) => api.refreshSignerSession(id, idempotencyKey)), (session) => {
        this.clearRefreshKey(id)
      this.showSecret("Signer session refreshed", session.token, "Replace the prior signer token now. This token cannot be shown again.")
      this.loadRoute()
      })
    }
    if (action === "revoke-session") this.runCommand(`revoke-session-${id}`, Effect.flatMap(UserApi, (api) => api.revokeSignerSession(id)), () => this.loadRoute())
  }
  private logout(): void {
    this.runCommand("logout", Effect.flatMap(UserApi, (api) => api.logout()), () => {
      this.clearSecret()
      this.auth = { tag: "signedOut" }
      this.notice = { tone: "status", text: msg("Signed out.") }
    })
  }
  private showSecret(heading: string, value: string, instructions: string): void {
    this.secret = { heading, value, instructions }
    this.updateComplete.then(() => {
      const dialog = this.renderRoot.querySelector<HTMLDialogElement>("dialog")
      if (this.isConnected && dialog !== null && !dialog.open) dialog.showModal()
    })
  }
  private clearSecret(): void {
    this.renderRoot.querySelector<HTMLDialogElement>("dialog")?.close()
    this.secret = undefined
  }
  private renderNotice(): TemplateResult | typeof nothing {
    if (this.notice === undefined) return nothing
    return this.notice.tone === "error"
      ? html`<p part="notice notice-danger" data-tone="danger" role="alert">${this.notice.text}</p>`
      : html`<p part="notice notice-success" data-tone="success" role="status">${this.notice.text}</p>`
  }
  private renderSignIn(): TemplateResult {
    return html`<section part="page auth-page" aria-labelledby="sign-in-heading"><header part="page-header"><div><p part="eyebrow">Secure account access</p><h2 id="sign-in-heading">${msg("Sign in")}</h2></div></header>
      <div part="auth-grid"><article part="surface auth-card"><h3>Email a one-time code</h3><p>No password is required. Codes expire after a short interval.</p>
        <form part="form" @submit=${this.requestCode}><label for="request-email">Email address</label>
          <input id="request-email" name="email" type="email" autocomplete="email" maxlength="320" required>
          <button type="submit" ?disabled=${this.inFlight.has("request-code")}>Send one-time code</button></form></article>
        <article part="surface auth-card"><h3>Verify your code</h3><form part="form" @submit=${this.verifyCode}><fieldset><legend>Verification details</legend>
          <label for="verify-email">Email address</label><input id="verify-email" name="email" type="email" autocomplete="email" maxlength="320" required>
          <label for="verify-code">Six-digit code</label><input id="verify-code" name="code" inputmode="numeric" .autocomplete=${"one-time-code"} pattern="[0-9]{6}" maxlength="6" required>
          </fieldset><button type="submit" ?disabled=${this.inFlight.has("verify-code")}>Verify and sign in</button></form></article></div>
      ${this.providers.some((provider) => provider !== "email") ? html`<nav part="surface provider-options" aria-label="Other sign-in options"><h3>Other sign-in options</h3><ul>${this.providers.map(renderProvider)}</ul></nav>` : nothing}
    </section>`
  }
  private renderUnscoped(session: AuthSession): TemplateResult {
    return html`<section part="page identity-page" aria-labelledby="link-heading"><header part="page-header"><div><p part="eyebrow">Identity connection</p><h2 id="link-heading">Connect your account</h2></div><strong part="badge badge-warning" data-tone="warning">Action required</strong></header>
      <article part="surface form-card"><p>Your verified identity is not connected to a clearinghouse account yet.</p><dl part="identity-details"><div><dt>Principal ID</dt><dd><code>${session.principal_id}</code></dd></div></dl>
      <form part="form" @submit=${this.redeem}><label for="invitation">One-time invitation</label>
        <input id="invitation" name="invitation" type="password" autocomplete="off" minlength="50" maxlength="128" required>
        <button type="submit" ?disabled=${this.inFlight.has("redeem")}>Connect identity</button></form></article></section>`
  }
  private renderOverview(): TemplateResult {
    return html`<section part="page overview-page" aria-labelledby="overview-heading"><header part="page-header"><div><p part="eyebrow">Account workspace</p><h2 id="overview-heading">${msg("Account overview")}</h2></div>${this.account === undefined ? nothing : html`<strong part=${this.account.status === "active" ? "badge badge-success" : "badge badge-warning"} data-tone=${this.account.status === "active" ? "success" : "warning"}>${this.account.status}</strong>`}</header>
      ${this.account === undefined || this.balance === undefined ? html`<p role="status">Loading account…</p>` : html`
        <article part="surface account-card"><p part="eyebrow">Payer account</p><h3>${this.account.display_name}</h3><p><code>${this.account.id}</code></p></article>
        <dl part="metrics"><div part="surface metric available-metric"><dt>Available to spend</dt><dd><data part="numeric-value" value=${this.balance.available.amount}>${this.balance.available.amount} ${this.balance.available.unit}</data></dd></div>
        <div part="surface metric"><dt>Posted balance</dt><dd><data value=${this.balance.posted.amount}>${this.balance.posted.amount} ${this.balance.posted.unit}</data></dd></div>
        <div part="surface metric"><dt>Open exposure</dt><dd><data value=${this.balance.open_lease_exposure.amount}>${this.balance.open_lease_exposure.amount} ${this.balance.open_lease_exposure.unit}</data></dd></div>
        <div part="surface metric"><dt>Account status</dt><dd>${this.account.status}</dd></div></dl>`}</section>`
  }
  private renderCredentials(): TemplateResult {
    return html`<section part="page credentials-page" aria-labelledby="credentials-heading"><header part="page-header"><div><p part="eyebrow">Programmatic access</p><h2 id="credentials-heading">Credentials</h2></div><strong part="badge badge-info" data-tone="info">${this.credentials.length} total</strong></header>
      <article part="form-card"><h3>Create an API credential</h3><form part="form" @submit=${this.issueCredential}><label for="credential-label">Credential label</label>
        <input id="credential-label" name="label" maxlength="200" required><button type="submit" ?disabled=${this.inFlight.has("issue-credential")}>Create credential</button></form></article>
      <section part="table-panel" aria-labelledby="credentials-table-heading"><h3 id="credentials-table-heading">Credential inventory</h3><div part="table-scroll" tabindex="0" role="region" aria-label="API credential inventory"><table><caption>Your API credentials</caption><thead><tr><th scope="col">Label</th><th scope="col">Prefix</th>
      <th scope="col">Status</th><th scope="col">Created</th><th scope="col">Actions</th></tr></thead>
      <tbody>${this.credentials.length === 0 ? html`<tr><td colspan="5">No API credentials.</td></tr>` : this.credentials.map(renderCredential)}</tbody></table></div></section></section>`
  }
  private renderSessions(): TemplateResult {
    return html`<section part="page sessions-page" aria-labelledby="sessions-heading"><header part="page-header"><div><p part="eyebrow">Bounded authorization</p><h2 id="sessions-heading">Signer sessions</h2></div><strong part="badge badge-info" data-tone="info">${this.sessions.length} open</strong></header>
      <article part="form-card"><h3>Open a signer session</h3><form part="form" @submit=${this.createSession}><fieldset><legend>Session limits</legend>
        <label for="capability">Capability</label><input id="capability" name="capability" maxlength="128" required>
        <label for="model">Model (optional)</label><input id="model" name="model" maxlength="512">
        <label for="app">Application</label><input id="app" name="app" maxlength="256" required>
        <label for="cap">Spend cap in wei</label><input id="cap" name="cap" inputmode="numeric" pattern="[1-9][0-9]*" required>
        <label for="ttl">Lifetime in seconds</label><input id="ttl" name="ttl" type="number" min="60" max="86400" value="3600" required>
        </fieldset><button type="submit" ?disabled=${this.inFlight.has("create-session")}>Create signer session</button></form></article>
      <section aria-labelledby="open-sessions-heading"><h3 id="open-sessions-heading">Open sessions</h3><div part="card-grid">${this.sessions.length === 0 ? html`<p part="surface empty-state">No signer sessions.</p>` : this.sessions.map(renderSession)}</div></section></section>`
  }
  private renderCatalog(): TemplateResult {
    return html`<section part="page catalog-page" aria-labelledby="catalog-heading"><header part="page-header"><div><p part="eyebrow">Pricing and policy</p><h2 id="catalog-heading">Capability catalog</h2></div><strong part="badge badge-info" data-tone="info">${this.catalog.length} rates</strong></header>
      <section part="table-panel" aria-labelledby="catalog-table-heading"><h3 id="catalog-table-heading">Available capabilities</h3><div part="table-scroll" tabindex="0" role="region" aria-label="Capability catalog"><table><caption>Capabilities and exact rates available to your account</caption><thead><tr><th scope="col">Capability</th>
      <th scope="col">Model</th><th scope="col">Rate</th><th scope="col">Policy</th></tr></thead>
      <tbody>${this.catalog.length === 0 ? html`<tr><td colspan="4">No capabilities are available.</td></tr>` : this.catalog.map(renderCatalogEntry)}</tbody></table></div></section></section>`
  }
  private renderUsage(): TemplateResult {
    return html`<section part="page usage-page" aria-labelledby="usage-heading"><header part="page-header"><div><p part="eyebrow">Metering ledger</p><h2 id="usage-heading">Usage</h2></div><strong part="badge badge-info" data-tone="info">${this.usage.length} events</strong></header><section part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="Settled usage"><table><caption>Settled usage for your account</caption>
      <thead><tr><th scope="col">Occurred</th><th scope="col">Capability</th><th scope="col">Quantity</th><th scope="col">Event</th></tr></thead>
      <tbody>${this.usage.length === 0 ? html`<tr><td colspan="4">No settled usage.</td></tr>` : this.usage.map(renderUsage)}</tbody></table></div>
      ${typeof this.usageCursor === "string" ? html`<p><button type="button" data-action="load-more-usage"
        ?disabled=${this.inFlight.has("load-more-usage")}>Load more usage</button></p>` : nothing}</section></section>`
  }
  private renderCharges(): TemplateResult {
    return html`<section part="page charges-page" aria-labelledby="charges-heading"><header part="page-header"><div><p part="eyebrow">Financial ledger</p><h2 id="charges-heading">Charges</h2></div><strong part="badge badge-info" data-tone="info">${this.charges.length} charges</strong></header><section part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="Settled charges"><table><caption>Settled charges for your account</caption>
      <thead><tr><th scope="col">Created</th><th scope="col">Amount</th><th scope="col">Usage event</th></tr></thead>
      <tbody>${this.charges.length === 0 ? html`<tr><td colspan="3">No settled charges.</td></tr>` : this.charges.map(renderCharge)}</tbody></table></div>
      ${typeof this.chargesCursor === "string" ? html`<p><button type="button" data-action="load-more-charges"
        ?disabled=${this.inFlight.has("load-more-charges")}>Load more charges</button></p>` : nothing}</section></section>`
  }
  private renderProfile(session: AuthSession): TemplateResult {
    return html`<section part="page profile-page" aria-labelledby="profile-heading"><header part="page-header"><div><p part="eyebrow">Identity and access</p><h2 id="profile-heading">Profile and security</h2></div><strong part="badge badge-success" data-tone="success">Signed in</strong></header><article part="surface profile-card"><h3>Browser session</h3><dl part="identity-details">
      <div><dt>Principal</dt><dd><code>${session.principal_id}</code></dd></div>
      <div><dt>Session expires</dt><dd><time datetime=${instantAttribute(session.expires_at)}>${formatInstant(session.expires_at)}</time></dd></div></dl>
      <p part="actions"><button type="button" data-action="refresh-browser" ?disabled=${this.inFlight.has("refresh-browser")}>Refresh browser session</button>
      <button type="button" data-action="logout" ?disabled=${this.inFlight.has("logout")}>Sign out</button></p></article></section>`
  }
  private renderRoute(session: AuthSession): TemplateResult {
    switch (this.route) {
      case "credentials": return this.renderCredentials()
      case "sessions": return this.renderSessions()
      case "catalog": return this.renderCatalog()
      case "usage": return this.renderUsage()
      case "charges": return this.renderCharges()
      case "profile": return this.renderProfile(session)
      case "overview": return this.renderOverview()
    }
  }
  private renderNavigation(): TemplateResult {
    return html`<p slot="navigation" part="navigation-heading">Account</p><ul slot="navigation" part="navigation-list">${userNavigation.map((item) => html`<li><a part=${this.route === item.route ? "navigation-link navigation-link-active" : "navigation-link"} href=${item.href}
      aria-current=${this.route === item.route ? "page" : "false"} @click=${this.navigate}>${renderNavigationIcon(item.icon)}<span part="navigation-label">${item.label}</span></a></li>`)}</ul>`
  }
  protected render(): TemplateResult {
    const metadata = userRouteMetadata(this.route)
    const session = this.auth.tag === "scoped" ? this.auth.session : undefined
    const scoped = session !== undefined
    return html`<och-app-shell @click=${this.handleAction} .heading=${scoped ? metadata.label : "Clearinghouse account"} .summary=${scoped ? metadata.summary : "Understand and control walletless Livepeer network spend."}
      exportparts="skip-link, shell, sidebar, header, sidebar-header, brand, brand-name, brand-accent, brand-subtitle, sidebar-close-button, sidebar-close-icon, sidebar-close-label, navigation, navigation-backdrop, workspace, topbar, menu-button, menu-icon, menu-label, mobile-brand, context, utility, main, heading-group, title, summary, content, footer, footer-note">
      ${scoped ? this.renderNavigation() : nothing}
      <p slot="context" part="account-context"><span part="account-context-label">${scoped ? "Payer account" : "Account console"}</span>${session === undefined ? nothing : html`<strong part="account-context-name">${this.account?.display_name ?? session.account_id}</strong>`}</p>
      ${scoped ? html`<p slot="utility" part="session-context"><strong part="badge badge-success" data-tone="success">Authenticated</strong></p>` : nothing}
      ${this.renderNotice()}
      ${this.auth.tag === "checking" ? html`<p role="status">Checking your session…</p>` : nothing}
      ${this.auth.tag === "denied" ? html`<section part="page state-page" aria-labelledby="denied-heading"><article part="surface state-card"><strong part="badge badge-danger" data-tone="danger">Access denied</strong><h2 id="denied-heading">Access denied</h2><p>This identity cannot use the account application.</p></article></section>` : nothing}
      ${this.auth.tag === "unavailable" ? html`<section part="page state-page" aria-labelledby="unavailable-heading"><article part="surface state-card"><strong part="badge badge-warning" data-tone="warning">Unavailable</strong><h2 id="unavailable-heading">Service unavailable</h2><p>Your session could not be checked safely. Try again later.</p></article></section>` : nothing}
      ${this.auth.tag === "signedOut" ? this.renderSignIn() : nothing}
      ${this.auth.tag === "unscoped" ? this.renderUnscoped(this.auth.session) : nothing}
      ${this.auth.tag === "scoped" ? this.renderRoute(this.auth.session) : nothing}
      ${this.secret === undefined ? nothing : html`<dialog part="dialog" @close=${this.clearSecret} aria-labelledby="secret-heading">
        <h2 id="secret-heading">${this.secret.heading}</h2><p>${this.secret.instructions}</p><p><code data-secret>${this.secret.value}</code></p>
        <form method="dialog"><button type="submit" data-action="close-secret">I saved it</button></form></dialog>`}
    </och-app-shell>`
  }
}
declare global { interface HTMLElementTagNameMap { "user-app": UserApp } }
