import {
  CursorPaginationController,
  failureMessage,
  formatAmount,
  parseDisplayAmount,
  readDisplayDenomination,
  subscribeDisplayDenomination,
  type DisplayDenomination
} from "@livepeer/clearinghouse-ui"
import { Effect } from "effect"
import { LitElement, css, html, nothing, svg, type PropertyValues, type TemplateResult } from "lit"
import { customElement, query, state } from "lit/decorators.js"
import { ApiFailure, InvalidPayload, NetworkFailure, forkUser, UserApi, type UserFailure } from "./api.js"
import type { Cost, Credential, Offer, Provider, Session, Summary, Workload } from "./contracts.js"
import { navigation, routeFromPath, routeMetadata, type UserIcon, type UserRoute } from "./navigation.js"
import { estimateQuote, type QuoteInputs } from "./quote-estimator.js"

type Auth = { readonly tag: "checking" | "out" } | { readonly tag: "in"; readonly session: Session }
type Notice = { readonly error: boolean; readonly text: string }
type Secret = { readonly heading: string; readonly guidance: string; readonly value: string }
const date = (value: string): string => new Intl.DateTimeFormat(document.documentElement.lang || "en", {
  dateStyle: "medium", timeStyle: "short"
}).format(new Date(value))
const rate = (offer: Offer, denomination: DisplayDenomination): string => {
  const amount = formatAmount(offer.price.numerator, offer.price.denominator, offer.price.currency, denomination)
  return `${amount.primary}${amount.exactWei ? ` (${amount.exactWei} exact)` : ""} per ${offer.price.quantity_unit}`
}
const statusLabel = (status: Workload["status"]): string => status.charAt(0).toUpperCase() + status.slice(1)
const initialQuoteInputs: QuoteInputs = Object.freeze({
  executions: "1", duration: "10", width: "1280", height: "720", frames: "1", fps: "30"
})

const icon = (name: UserIcon): TemplateResult => {
  const path = name === "pulse" ? svg`<path d="M3 13h4l2.5-7 5 12 2.5-6h4"/>`
    : name === "catalog" ? svg`<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 17.5h7"/>`
      : name === "calculator" ? svg`<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M8 6h8M8 11h2M14 11h2M8 15h2M14 15h2M8 19h2M14 19h2"/>`
      : name === "workload" ? svg`<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9h10M7 13h6"/>`
        : name === "key" ? svg`<circle cx="8" cy="12" r="4"/><path d="m12 12 9-9M17 7l2 2"/>`
          : name === "activity" ? svg`<path d="M4 20V10M9 20V4M14 20v-7M19 20V7"/>`
            : svg`<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/>`
  return svg`<svg part="navigation-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${path}</svg>`
}

@customElement("user-app")
export class UserApp extends LitElement {
  static styles = css`
    *, *::before, *::after { box-sizing: border-box; }
    :host, [part~="page"], [part~="surface"], [part~="form-card"], [part~="estimator-grid"], form, fieldset, dl { display: grid; gap: var(--och-user-content-gap); }
    [part~="page-header"], [part~="actions"] { display: flex; align-items: center; justify-content: space-between; gap: var(--och-user-content-gap); }
    [part~="metrics"], [part~="card-grid"], [part~="auth-grid"], [part~="field-grid"] { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--och-user-card-min-size)), 1fr)); gap: var(--och-user-content-gap); }
    [part~="table-scroll"] { overflow-inline: auto; }
    table { inline-size: 100%; border-collapse: collapse; }
    th, td { padding: var(--och-user-table-cell-padding); text-align: start; border-block-end: var(--och-border-width) solid var(--och-color-border); }
    input, select, button { min-block-size: var(--och-control-min-block-size); padding: var(--och-user-control-padding); color: var(--och-color-text); background: var(--och-user-control-background); border: var(--och-border-width) solid var(--och-user-control-border); border-radius: var(--och-radius-small); }
    fieldset { padding: 0; border: 0; }
    code { overflow-wrap: anywhere; }
    dialog { inline-size: min(var(--och-user-dialog-max-size), calc(100vi - 2 * var(--och-space-3))); }
  `
  @state() private auth: Auth = { tag: "checking" }
  @state() private providers: ReadonlyArray<Provider> = ["email"]
  @state() private route: UserRoute = routeFromPath(location.pathname)
  @state() private offers: ReadonlyArray<Offer> = []
  @state() private workloads: ReadonlyArray<Workload> = []
  @state() private credentials: ReadonlyArray<Credential> = []
  @state() private costs: ReadonlyArray<Cost> = []
  @state() private summary: Summary | undefined
  @state() private capabilityFilter = ""
  @state() private modelFilter = ""
  @state() private estimateOfferId = ""
  @state() private quoteInputs: QuoteInputs = initialQuoteInputs
  @state() private notice: Notice | undefined = undefined
  @state() private secret: Secret | undefined
  @state() private busy = false
  @state() private routeLoading = false
  @state() private denomination: DisplayDenomination = "wei"
  @query("dialog") private secretDialog?: HTMLDialogElement
  private unsubscribeDenomination: (() => void) | undefined
  private readonly pagination = new CursorPaginationController(this)
  private routeLoad = 0

  connectedCallback(): void {
    super.connectedCallback?.()
    addEventListener("popstate", this.onPopState)
    this.syncOfferFilters()
    this.denomination = readDisplayDenomination()
    this.unsubscribeDenomination = subscribeDisplayDenomination((value) => { this.denomination = value })
    this.loadIdentity()
  }
  disconnectedCallback(): void {
    removeEventListener("popstate", this.onPopState)
    this.unsubscribeDenomination?.()
    this.unsubscribeDenomination = undefined
    super.disconnectedCallback?.()
  }
  private readonly onPopState = (): void => {
    this.route = routeFromPath(location.pathname)
    this.syncOfferFilters()
    this.pagination.restore()
    this.loadRoute()
  }
  private syncOfferFilters(): void {
    const query = new URL(location.href).searchParams
    this.capabilityFilter = query.get("capability") ?? ""
    this.modelFilter = query.get("model") ?? ""
  }
  private run<A>(effect: Effect.Effect<A, UserFailure, UserApi>, success: (value: A) => void, complete: () => void = () => undefined, active: () => boolean = () => true): void {
    forkUser(effect, (value) => {
      if (this.isConnected && active()) success(value)
      complete()
    }, (failure) => {
      if (this.isConnected && active()) this.fail(failure)
      complete()
    })
  }
  private fail(failure: UserFailure): void {
    if (failure instanceof ApiFailure && failure.status === 401) {
      this.auth = { tag: "out" }
      this.notice = undefined
      return
    }
    if (failure instanceof ApiFailure && failure.status === 409) {
      this.pagination.reset()
      this.notice = { error: true, text: failureMessage(failure.operation, "conflict") }
      this.loadRoute()
      return
    }
    if (this.auth.tag === "checking") this.auth = { tag: "out" }
    const reason = failure instanceof InvalidPayload ? "invalid-response"
      : failure instanceof NetworkFailure || failure.status >= 500 ? "unavailable"
        : failure.status === 403 ? "access"
          : failure.status === 400 || failure.status === 422 ? "rejected" : "unknown"
    this.notice = { error: true, text: failureMessage(failure.operation, reason) }
  }
  private loadIdentity(): void {
    forkUser(Effect.flatMap(UserApi, (api) => api.providers()), (providers) => {
      if (this.isConnected) this.providers = providers.providers
    }, () => undefined)
    this.run(Effect.flatMap(UserApi, (api) => api.session()), (session) => {
      this.auth = { tag: "in", session }
      this.loadRoute()
    })
  }
  private loadRoute(): void {
    if (this.auth.tag !== "in") return
    const route = this.route
    const load = ++this.routeLoad
    this.routeLoading = true
    const cursor = this.pagination.cursor
    const capability = this.capabilityFilter || null
    const model = this.modelFilter || null
    this.run(Effect.gen(function* () {
      const api = yield* UserApi
      if (route === "discovery" || route === "estimate") return { tag: "offers" as const, value: yield* api.offers(cursor, capability, model) }
      if (route === "workloads") return { tag: "workloads" as const, value: yield* Effect.all({ offers: api.offers(), workloads: api.workloads(cursor) }, { concurrency: 2 }) }
      if (route === "credentials") return { tag: "credentials" as const, value: yield* api.credentials(cursor) }
      if (route === "usage") return { tag: "usage" as const, value: yield* Effect.all({ summary: api.summary(), costs: api.costs(cursor) }, { concurrency: 2 }) }
      return { tag: "overview" as const, value: yield* api.summary() }
    }), (result) => {
      if (result.tag === "offers") {
        this.offers = result.value.items
        this.pagination.received(result.value.next_cursor)
        if (!result.value.items.some((offer) => offer.id === this.estimateOfferId)) this.estimateOfferId = result.value.items[0]?.id ?? ""
      }
      if (result.tag === "workloads") { this.offers = result.value.offers.items; this.workloads = result.value.workloads.items; this.pagination.received(result.value.workloads.next_cursor) }
      if (result.tag === "credentials") { this.credentials = result.value.items; this.pagination.received(result.value.next_cursor) }
      if (result.tag === "usage") { this.summary = result.value.summary; this.costs = result.value.costs.items; this.pagination.received(result.value.costs.next_cursor) }
      if (result.tag === "overview") this.summary = result.value
    }, () => { if (load === this.routeLoad) this.routeLoading = false }, () => load === this.routeLoad)
  }
  private navigate(event: MouseEvent): void {
    if (!(event.currentTarget instanceof HTMLAnchorElement)) return
    event.preventDefault()
    history.pushState(null, "", event.currentTarget.href)
    this.pagination.reset()
    this.onPopState()
  }
  private changePage(event: CustomEvent<{ readonly direction: "next" | "previous" }>): void {
    if (this.pagination.move(event.detail.direction)) this.loadRoute()
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
  private filterOffers(event: SubmitEvent): void {
    event.preventDefault()
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    const values = new FormData(event.currentTarget)
    this.capabilityFilter = String(values.get("capability") ?? "").trim()
    this.modelFilter = String(values.get("model") ?? "").trim()
    const url = new URL(location.href)
    const filters: ReadonlyArray<readonly [string, string]> = [
      ["capability", this.capabilityFilter], ["model", this.modelFilter]
    ]
    for (const [name, value] of filters) {
      if (value) url.searchParams.set(name, value)
      else url.searchParams.delete(name)
    }
    history.replaceState(history.state, "", url)
    this.pagination.reset()
    this.loadRoute()
  }
  private submit(event: SubmitEvent, kind: "request" | "verify" | "credential" | "workload"): void {
    event.preventDefault()
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    if (this.busy) return
    this.busy = true
    const form = event.currentTarget
    const values = new FormData(form)
    const spend = kind === "workload"
      ? parseDisplayAmount(String(values.get("max-spend") ?? ""), this.denomination)
      : { _tag: "Empty" as const }
    if (spend._tag === "Invalid") {
      this.notice = { error: true, text: spend.message }
      this.busy = false
      return
    }
    const effect = Effect.gen(function* () {
      const api = yield* UserApi
      if (kind === "request") {
        yield* api.requestCode(String(values.get("email") ?? ""))
        return { tag: "request" as const }
      }
      if (kind === "verify") return {
        tag: "verify" as const,
        value: yield* api.verifyCode(String(values.get("email") ?? ""), String(values.get("code") ?? ""))
      }
      if (kind === "credential") return {
        tag: "credential" as const,
        value: yield* api.createCredential(String(values.get("name") ?? ""))
      }
      return {
        tag: "workload" as const,
        value: yield* api.createWorkload(
          String(values.get("offer") ?? ""),
          String(values.get("reference") ?? ""),
          spend._tag === "Valid" ? spend.wei : undefined
        )
      }
    })
    this.run(effect, (result) => {
      if (result.tag === "verify") this.auth = { tag: "in", session: result.value }
      if (result.tag === "credential") this.secret = {
        heading: "Account API credential created",
        guidance: "Use this och_live_… value for account-scoped Clearinghouse control-plane requests. It remains valid until you revoke it.",
        value: result.value.token
      }
      if (result.tag === "workload") this.secret = {
        heading: "Workload SDK token created",
        guidance: "Pass this token only to livepeer-python-gateway for this quoted workload. It expires with the workload and stops authorizing if you revoke the workload.",
        value: result.value.sdk_token
      }
      const text = kind === "request" ? "A one-time code was sent. Enter the code below to continue."
        : kind === "verify" ? "Signed in. Your account overview is ready."
          : kind === "credential" ? "Account API credential created. Save its value before closing the dialog."
            : "Workload access created. Save the workload SDK token before closing the dialog."
      this.notice = { error: false, text }
      form.reset()
      this.loadRoute()
    }, () => { this.busy = false })
  }
  private requestCode(event: SubmitEvent): void { this.submit(event, "request") }
  private verifyCode(event: SubmitEvent): void { this.submit(event, "verify") }
  private createCredential(event: SubmitEvent): void { this.submit(event, "credential") }
  private createWorkload(event: SubmitEvent): void { this.submit(event, "workload") }
  private money(numerator: string | bigint, denominator: string | bigint, currency: string): TemplateResult {
    const amount = formatAmount(numerator, denominator, currency, this.denomination)
    return html`<data value=${numerator.toString()}>${amount.primary}</data>${amount.exactWei ? html` <small>(${amount.exactWei} exact)</small>` : nothing}`
  }
  private spendCeilingField(prefix: string): TemplateResult {
    const unit = this.denomination === "eth" ? "ETH" : "wei"
    return html`<label for=${`${prefix}-max-spend`}>Maximum spend (${unit}, optional)</label><input id=${`${prefix}-max-spend`} name="max-spend" inputmode="decimal" autocomplete="off" aria-describedby=${`${prefix}-max-spend-help`}><p id=${`${prefix}-max-spend-help`} part="hint"><small>Sets an immutable enforced ceiling for this workload. The signer reserves each payment request before returning tickets and rejects work that could exceed the remaining amount. Leave blank for no spend ceiling.</small></p>`
  }
  private chooseEstimateOffer(event: Event): void {
    if (event.currentTarget instanceof HTMLSelectElement) this.estimateOfferId = event.currentTarget.value
  }
  private updateQuoteInput(event: Event): void {
    if (!(event.currentTarget instanceof HTMLInputElement)) return
    const value = event.currentTarget.value
    const name = event.currentTarget.name
    if (name === "executions") this.quoteInputs = { ...this.quoteInputs, executions: value }
    if (name === "duration") this.quoteInputs = { ...this.quoteInputs, duration: value }
    if (name === "width") this.quoteInputs = { ...this.quoteInputs, width: value }
    if (name === "height") this.quoteInputs = { ...this.quoteInputs, height: value }
    if (name === "frames") this.quoteInputs = { ...this.quoteInputs, frames: value }
    if (name === "fps") this.quoteInputs = { ...this.quoteInputs, fps: value }
  }
  private action(event: MouseEvent): void {
    if (!(event.target instanceof HTMLButtonElement)) return
    const action = event.target.dataset.action
    const id = event.target.dataset.id
    if (action === "close-secret") { this.secretDialog?.close(); this.secret = undefined; return }
    if (this.busy) return
    if (action === "logout") {
      this.busy = true
      this.run(Effect.flatMap(UserApi, (api) => api.logout()), () => {
        this.auth = { tag: "out" }
        this.notice = { error: false, text: "Signed out. Sign in again to access your account." }
      }, () => { this.busy = false })
      return
    }
    if (!id) return
    this.busy = true
    const revokeCredential = action === "revoke-credential"
    const effect = Effect.flatMap(UserApi, (api) => revokeCredential
      ? api.revokeCredential(id) : api.revokeWorkload(id))
    this.run(effect, () => {
      this.notice = {
        error: false,
        text: revokeCredential ? "Account API credential revoked. It can no longer authorize account requests."
          : "Workload access revoked. Its workload SDK token can no longer authorize signer requests."
      }
      this.loadRoute()
    }, () => { this.busy = false })
  }
  private signIn(): TemplateResult {
    return html`<section part="page auth-page" aria-labelledby="sign-in"><header part="page-header"><div><p part="eyebrow">Secure account access</p><h2 id="sign-in">Sign in</h2></div></header>
      <p>Sign in to compare network offers, create quoted workload access, and review signer-reported costs for your personal account.</p>
      <div part="auth-grid"><article part="surface"><h3>Request a one-time code</h3><p id="request-code-help" part="hint"><small>Enter the email address for your account. We will send a six-digit sign-in code to that address.</small></p><form @submit=${this.requestCode}><label for="email">Account email address</label><input id="email" name="email" type="email" autocomplete="email" aria-describedby="request-code-help" required><button type="submit" ?disabled=${this.busy}>Email a one-time code</button></form></article>
      <article part="surface"><h3>Enter your one-time code</h3><p id="verify-code-help" part="hint"><small>Use the same email address and the six-digit code from the latest sign-in message.</small></p><form @submit=${this.verifyCode}><label for="verify-email">Account email address</label><input id="verify-email" name="email" type="email" autocomplete="email" aria-describedby="verify-code-help" required><label for="code">Six-digit sign-in code</label><input id="code" name="code" inputmode="numeric" .autocomplete=${"one-time-code"} pattern="[0-9]{6}" aria-describedby="verify-code-help" required><button type="submit" ?disabled=${this.busy}>Verify code and sign in</button></form></article></div>
      ${this.providers.length > 1 ? html`<nav part="surface" aria-label="Other sign-in options"><h3>Other sign-in options</h3><ul>${this.providers.filter((provider) => provider !== "email").map((provider) => html`<li><a href=${`/v1/auth/oauth/${provider}/start`}>Continue with ${provider === "google" ? "Google" : "GitHub"}</a></li>`)}</ul></nav>` : nothing}</section>`
  }
  private overview(session: Session): TemplateResult {
    const summary = this.summary
    return html`<section part="page" aria-labelledby="overview"><header part="page-header"><div><p part="eyebrow">Account workspace</p><h2 id="overview">Clearinghouse overview</h2></div><strong part="badge badge-success">Signed in</strong></header>
      <p>Review your account activity, then compare a network offer or inspect an existing workload before authorizing more Livepeer work.</p>
      <article part="surface" aria-labelledby="signed-in-account"><h3 id="signed-in-account">Signed-in account</h3><p><bdi>${session.email}</bdi></p><p>Account identifier <code>${session.account_id}</code></p></article>
      <section aria-labelledby="account-summary"><h3 id="account-summary">Account activity</h3><p>These totals show the network access and measured cost currently associated with your account.</p>${this.routeLoading || summary === undefined ? html`<p role="status">Loading account activity…</p>` : html`<dl part="metrics"><div part="surface metric"><dt>Available network offers</dt><dd>${summary.offers}</dd></div><div part="surface metric"><dt>Active workloads</dt><dd>${summary.active_workloads}</dd></div><div part="surface metric"><dt>Signer-reported cost <och-help-tip term="signer-reported cost">The fee reported by signer metering for usage attributed to your workloads.</och-help-tip></dt><dd>${this.money(summary.computed_fee, 1n, summary.currency)}</dd></div></dl>`}</section>
      <p><a href="/discovery" @click=${this.navigate}>Compare network offers</a> or <a href="/workloads" @click=${this.navigate}>review workload access</a>.</p></section>`
  }
  private discoveryPage(): TemplateResult {
    const filtered = this.capabilityFilter.length > 0 || this.modelFilter.length > 0
    return html`<section part="page" aria-labelledby="network"><header part="page-header"><div><p part="eyebrow">Livepeer network</p><h2 id="network">Network offers</h2></div><strong part="badge badge-info">${this.routeLoading ? "Loading" : `${this.offers.length} on this page`}</strong></header>
      <p>Compare current network offers by capability, runner, and advertised rate before creating workload access.</p>
      <search><form part="surface" @submit=${this.filterOffers} aria-labelledby="network-filters"><h3 id="network-filters">Filter network offers</h3><p id="network-filter-help" part="hint"><small>Enter a capability or model name to narrow this page. Leave both fields empty to show all available offers.</small></p><label for="capability-filter">Capability name</label><input id="capability-filter" name="capability" .value=${this.capabilityFilter} aria-describedby="network-filter-help"><label for="model-filter">Model name</label><input id="model-filter" name="model" .value=${this.modelFilter} aria-describedby="network-filter-help"><button type="submit">Filter network offers</button></form></search>
      <section aria-labelledby="network-results"><h3 id="network-results">Available network offers</h3><p>A network offer identifies the runner endpoint and exact advertised rate available when discovery observed it.</p>${this.routeLoading ? html`<p role="status">Loading network offers…</p>` : this.offers.length === 0 ? html`<article part="surface"><h4>${filtered ? "No network offers match these filters" : "No network offers are available"}</h4><p>${filtered ? "Clear or change the filters, then filter network offers again." : "Try again later or contact the clearinghouse operator if offers should be available."}</p></article>` : html`<div part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="Network offers"><table><caption>Current network offers with runner endpoints and advertised rates</caption><thead><tr><th scope="col">Capability</th><th scope="col">Model</th><th scope="col">Runner</th><th scope="col">Advertised rate <och-help-tip term="advertised rate">The exact price published with a network offer when discovery observed it.</och-help-tip></th></tr></thead><tbody>${this.offers.map((offer) => html`<tr><th scope="row">${offer.capability}</th><td>${offer.model ?? "Any model"}</td><td><code>${offer.runner_url}</code></td><td>${this.money(offer.price.numerator, offer.price.denominator, offer.price.currency)} per ${offer.price.quantity_unit}</td></tr>`)}</tbody></table></div></div>`}${this.paginationControl()}<p><a href="/estimate" @click=${this.navigate}>Estimate a workload from a network offer</a>.</p></section></section>`
  }
  private estimateFields(unit: string): TemplateResult {
    const durationLabel = unit === "hour" || unit === "hours" ? "Expected runtime in hours" : "Expected runtime in seconds"
    const duration = html`<label for="duration">${durationLabel}</label><input id="duration" name="duration" type="number" min="0.000000001" step="0.000000001" .value=${this.quoteInputs.duration} @input=${this.updateQuoteInput} aria-describedby="duration-help" required><p id="duration-help" part="hint"><small>Enter the expected runtime for one execution using the unit named in the label.</small></p>`
    const dimensions = html`<label for="width">Output width in pixels</label><input id="width" name="width" type="number" min="1" step="1" .value=${this.quoteInputs.width} @input=${this.updateQuoteInput} aria-describedby="dimensions-help" required><label for="height">Output height in pixels</label><input id="height" name="height" type="number" min="1" step="1" .value=${this.quoteInputs.height} @input=${this.updateQuoteInput} aria-describedby="dimensions-help" required><p id="dimensions-help" part="hint"><small>Use the intended output dimensions for each frame or image.</small></p>`
    if (["second", "seconds", "hour", "hours"].includes(unit)) return duration
    if (["pixel", "pixels"].includes(unit)) return html`${dimensions}<label for="frames">Frames or images per execution</label><input id="frames" name="frames" type="number" min="1" step="1" .value=${this.quoteInputs.frames} @input=${this.updateQuoteInput} aria-describedby="frames-help" required><p id="frames-help" part="hint"><small>Enter the total number of frames or still images produced by one execution.</small></p>`
    if (unit === "720p-pixel-seconds") return html`${dimensions}<label for="fps">Frames per second</label><input id="fps" name="fps" type="number" min="1" step="1" .value=${this.quoteInputs.fps} @input=${this.updateQuoteInput} aria-describedby="fps-help" required><p id="fps-help" part="hint"><small>Enter the playback or processing frame rate for the workload.</small></p>${duration}`
    return html`<p part="hint">This offer does not publish a billing unit the estimator understands.</p>`
  }
  private estimatePage(): TemplateResult {
    const selected = this.offers.find((offer) => offer.id === this.estimateOfferId) ?? this.offers[0]
    if (selected === undefined) return html`<section part="page" aria-labelledby="estimate"><header part="page-header"><div><p part="eyebrow">Plan before authorizing</p><h2 id="estimate">Cost estimator</h2></div></header><p>Estimate a workload from a current network offer and your usage assumptions before creating time-bounded access.</p><search><form part="surface" @submit=${this.filterOffers} aria-labelledby="empty-estimate-filters"><h3 id="empty-estimate-filters">Find a network offer</h3><p id="empty-estimate-filter-help" part="hint"><small>Enter a capability or model name, or leave both fields empty to search all current offers.</small></p><label for="empty-capability-filter">Capability name</label><input id="empty-capability-filter" name="capability" .value=${this.capabilityFilter} aria-describedby="empty-estimate-filter-help"><label for="empty-model-filter">Model name</label><input id="empty-model-filter" name="model" .value=${this.modelFilter} aria-describedby="empty-estimate-filter-help"><button type="submit">Find network offers</button></form></search>${this.routeLoading ? html`<p role="status">Loading network offers for the estimator…</p>` : html`<article part="surface"><h3>No priced network offers found</h3><p>The estimator needs a current network offer with an exact advertised rate. Change or clear the filters and try again.</p></article>`}${this.paginationControl()}</section>`
    const unit = selected.price.quantity_unit.trim().toLowerCase()
    const estimate = estimateQuote(selected.price, this.quoteInputs)
    return html`<section part="page" aria-labelledby="estimate"><header part="page-header"><div><p part="eyebrow">Plan before authorizing</p><h2 id="estimate">Cost estimator</h2></div><strong part="badge badge-info">Exact rate arithmetic</strong></header>
      <p>Choose a current network offer and describe the expected usage. The estimate helps you compare options; it does not create access until you submit the workload form.</p><search><form part="surface" @submit=${this.filterOffers} aria-labelledby="estimate-filters"><h3 id="estimate-filters">Find another network offer</h3><p id="estimate-filter-help" part="hint"><small>Filter by capability or model without changing your usage assumptions.</small></p><label for="estimate-capability-filter">Capability name</label><input id="estimate-capability-filter" name="capability" .value=${this.capabilityFilter} aria-describedby="estimate-filter-help"><label for="estimate-model-filter">Model name</label><input id="estimate-model-filter" name="model" .value=${this.modelFilter} aria-describedby="estimate-filter-help"><button type="submit">Find network offers</button></form></search>
      <div part="estimator-grid"><form part="form-card" @submit=${this.createWorkload} aria-labelledby="estimate-inputs"><h3 id="estimate-inputs">Estimate and create workload access</h3><fieldset><legend>Network offer</legend><label for="estimate-offer">Capability and runner</label><select id="estimate-offer" name="offer" .value=${selected.id} @change=${this.chooseEstimateOffer} aria-describedby="offer-help" required>${this.offers.map((offer) => html`<option value=${offer.id}>${offer.capability}${offer.model ? ` / ${offer.model}` : ""} — ${rate(offer, this.denomination)}</option>`)}</select><p id="offer-help" part="hint"><small>Runner endpoint: <code>${selected.runner_url}</code>. The selected advertised rate is snapshotted when you create the workload.</small></p></fieldset>
        <fieldset><legend>Usage assumptions</legend><div part="field-grid"><label for="executions">Number of executions</label><input id="executions" name="executions" type="number" min="1" step="1" .value=${this.quoteInputs.executions} @input=${this.updateQuoteInput} aria-describedby="executions-help" required><p id="executions-help" part="hint"><small>Enter the whole number of times you expect the workload to run.</small></p>${this.estimateFields(unit)}</div></fieldset>
        <label for="estimate-reference">Job reference (optional)</label><input id="estimate-reference" name="reference" maxlength="256" autocomplete="off" aria-describedby="estimate-reference-help"><p id="estimate-reference-help" part="hint"><small>Use a reference you recognize later. The workload SDK token is limited to this quoted workload, expires with it, and is shown only once.</small></p>${this.spendCeilingField("estimate")}<button type="submit" ?disabled=${this.busy || estimate._tag !== "Ready"}>Create workload access at this rate</button></form>
        <article part="surface estimate-result" aria-labelledby="estimate-result"><h3 id="estimate-result">Estimated cost</h3><p>This result applies the advertised rate to the assumptions you entered.</p>${estimate._tag === "Ready" ? html`<dl part="estimate-summary"><div part="estimate-row"><dt part="estimate-term">Advertised rate <och-help-tip term="advertised rate">The exact price published with the selected network offer.</och-help-tip></dt><dd part="estimate-value">${this.money(selected.price.numerator, selected.price.denominator, selected.price.currency)} per ${selected.price.quantity_unit}</dd></div><div part="estimate-row"><dt part="estimate-term">Estimated quantity</dt><dd part="estimate-value"><data value=${estimate.quantity}>${estimate.quantity}</data> ${estimate.quantityUnit}${estimate.quantity === "1" ? "" : "s"}</dd></div><div part="estimate-row"><dt part="estimate-term">Estimated cost</dt><dd part="estimate-value"><output for="estimate-offer executions duration width height frames fps">${this.money(estimate.cost, 1n, selected.price.currency)}</output></dd></div><div part="estimate-row"><dt part="estimate-term">Network offer expires</dt><dd part="estimate-value"><time datetime=${selected.expires_at}>${date(selected.expires_at)}</time></dd></div></dl>` : estimate._tag === "Invalid" ? html`<p part="notice notice-danger" role="status">${estimate.message} Correct the usage assumptions to calculate an estimate.</p>` : html`<p part="notice notice-danger" role="status">Billing unit <code>${estimate.unit}</code> is not supported by this estimator. Select another network offer to continue.</p>`}<p part="hint"><small>This is an estimated cost, not a final charge. The workload keeps the selected advertised rate; signer-measured usage determines the signer-reported cost.</small></p></article></div>${this.paginationControl()}</section>`
  }
  private workloadsPage(): TemplateResult {
    return html`<section part="page" aria-labelledby="workloads"><header part="page-header"><div><p part="eyebrow">Quoted access</p><h2 id="workloads">Workloads</h2></div></header>
      <p>Create time-bounded access from a current network offer, or review and revoke access you already created.</p>
      <article part="form-card"><h3>Create workload access</h3><p id="workload-form-help" part="hint"><small>A workload SDK token configures <code>livepeer-python-gateway</code> for one quoted workload. It expires when access ends, stops authorizing when the workload is revoked, and is shown only once.</small></p><form @submit=${this.createWorkload}><label for="offer">Network offer</label><select id="offer" name="offer" aria-describedby="workload-offer-help workload-form-help" required>${this.offers.map((offer) => html`<option value=${offer.id}>${offer.capability} — ${rate(offer, this.denomination)}</option>`)}</select><p id="workload-offer-help" part="hint"><small>Select the capability and advertised rate to snapshot for this workload.</small></p><label for="reference">Job reference (optional)</label><input id="reference" name="reference" maxlength="256" aria-describedby="workload-reference-help"><p id="workload-reference-help" part="hint"><small>Add a reference you will recognize when reviewing usage and cost.</small></p>${this.spendCeilingField("workload")}<button type="submit" ?disabled=${this.busy || this.offers.length === 0}>Create workload access</button></form></article>
      <section aria-labelledby="existing-workloads"><h3 id="existing-workloads">Existing workloads</h3><p>Each workload keeps the advertised rate and optional spend ceiling captured when its access was created.</p>${this.routeLoading ? html`<p role="status">Loading workloads…</p>` : this.workloads.length === 0 ? html`<article part="surface"><h4>No workloads yet</h4><p>Choose a network offer above to create your first quoted workload and receive its workload SDK token.</p></article>` : html`<div part="card-grid">${this.workloads.map((workload) => html`<article part="surface"><h4>${workload.capability}</h4><p>Workload identifier <code>${workload.id}</code></p><p>${workload.client_reference ? html`Job reference <bdi>${workload.client_reference}</bdi>` : "No job reference was provided."}</p><p>${workload.max_spend_wei === null ? "No spend ceiling is enforced." : html`Maximum spend ${this.money(workload.max_spend_wei, 1n, "wei")}`}</p><p>Access expires <time datetime=${workload.expires_at}>${date(workload.expires_at)}</time></p><strong part=${workload.status === "active" ? "badge badge-success" : "badge"}>${statusLabel(workload.status)}</strong>${workload.status === "active" ? html`<button type="button" data-action="revoke-workload" data-id=${workload.id} ?disabled=${this.busy}>Revoke workload access</button>` : nothing}</article>`)}</div>${this.paginationControl()}`}</section></section>`
  }
  private credentialsPage(): TemplateResult {
    return html`<section part="page" aria-labelledby="credentials"><header part="page-header"><div><p part="eyebrow">Programmatic account access</p><h2 id="credentials">Account API credentials</h2></div></header>
      <p>Create credentials for software that calls Clearinghouse account APIs, and revoke credentials that should no longer have access.</p>
      <article part="form-card"><h3>Create an account API credential</h3><p id="credential-form-help" part="hint"><small>Account API credentials begin with <code>och_live_</code>, authorize account-scoped Clearinghouse requests, and remain valid until revoked. The plaintext value is shown only once.</small></p><form @submit=${this.createCredential}><label for="name">Credential name</label><input id="name" name="name" maxlength="80" aria-describedby="credential-name-help credential-form-help" required><p id="credential-name-help" part="hint"><small>Use a name that identifies the application or environment receiving this credential.</small></p><button type="submit" ?disabled=${this.busy}>Create account API credential</button></form></article>
      <section aria-labelledby="issued-credentials"><h3 id="issued-credentials">Issued account API credentials</h3><p>Review when each credential was created and revoke any credential that is no longer needed.</p>${this.routeLoading ? html`<p role="status">Loading account API credentials…</p>` : this.credentials.length === 0 ? html`<article part="surface"><h4>No account API credentials yet</h4><p>Create a credential above when an application needs to call Clearinghouse account APIs.</p></article>` : html`<div part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="Issued account API credentials"><table><caption>Issued account API credentials and their creation times</caption><thead><tr><th scope="col">Name</th><th scope="col">Created</th><th scope="col">Action</th></tr></thead><tbody>${this.credentials.map((credential) => html`<tr><th scope="row"><bdi>${credential.name}</bdi></th><td><time datetime=${credential.created_at}>${date(credential.created_at)}</time></td><td><button type="button" data-action="revoke-credential" data-id=${credential.id} ?disabled=${this.busy}>Revoke account API credential</button></td></tr>`)}</tbody></table></div></div>${this.paginationControl()}`}</section></section>`
  }
  private usagePage(): TemplateResult {
    return html`<section part="page" aria-labelledby="usage"><header part="page-header"><div><p part="eyebrow">Measured usage</p><h2 id="usage">Usage and cost</h2></div></header>
      <p>Compare measured quantities, quoted costs, and signer-reported costs for usage attributed to each workload.</p>
      <section aria-labelledby="workload-costs"><h3 id="workload-costs">Cost by workload</h3><p>Quoted cost uses each workload's snapshotted rate. Signer-reported cost is reconciled from metering; pending cost is authorized exposure that has not yet appeared in metering.</p>${this.routeLoading ? html`<p role="status">Loading usage and cost…</p>` : this.costs.length === 0 ? html`<article part="surface"><h4>No workloads to report</h4><p>Cost and remaining-budget records appear after you create workload access. Review your workloads or create one from a current offer.</p></article>` : html`<div part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="Workload costs"><table><caption>Measured, attributed, pending, and remaining cost by workload</caption><thead><tr><th scope="col">Workload</th><th scope="col">Measured quantity</th><th scope="col">Quoted cost</th><th scope="col">Signer-reported cost <och-help-tip term="signer-reported cost">The fee reported by signer metering for a workload's attributed usage.</och-help-tip></th><th scope="col">Pending <och-help-tip term="pending cost">Authorized signer exposure not yet reconciled to a metering event.</och-help-tip></th><th scope="col">Ceiling</th><th scope="col">Remaining</th></tr></thead><tbody>${this.costs.map((cost) => html`<tr><th scope="row"><code>${cost.workload.id}</code></th><td>${cost.measured_quantity} ${cost.measured_unit}</td><td>${this.money(cost.quoted_fee, 1n, cost.currency)}</td><td>${this.money(cost.computed_fee, 1n, cost.currency)}</td><td>${this.money(cost.pending_fee, 1n, cost.currency)}</td><td>${cost.spend_ceiling === null ? "Not set" : this.money(cost.spend_ceiling, 1n, cost.currency)}</td><td>${cost.remaining_spend === null ? "Not limited" : this.money(cost.remaining_spend, 1n, cost.currency)}</td></tr>`)}</tbody></table></div></div>${this.paginationControl()}<p>${this.summary?.usage_events ?? 0} usage events attributed to this account.</p>`}<p><a href="/workloads" @click=${this.navigate}>Review workload access</a>.</p></section></section>`
  }
  private profile(session: Session): TemplateResult {
    return html`<section part="page" aria-labelledby="profile"><header part="page-header"><div><p part="eyebrow">Identity and session</p><h2 id="profile">Profile and security</h2></div></header><p>Confirm which user and personal account are active in this browser, or end the current session.</p><article part="surface" aria-labelledby="identity-details"><h3 id="identity-details">Signed-in identity</h3><dl><div><dt>Email address</dt><dd><bdi>${session.email}</bdi></dd></div><div><dt>User identifier</dt><dd><code>${session.user_id}</code></dd></div><div><dt>Account identifier</dt><dd><code>${session.account_id}</code></dd></div><div><dt>Session expires</dt><dd><time datetime=${session.expires_at}>${date(session.expires_at)}</time></dd></div></dl><p>Signing out ends access in this browser. It does not revoke account API credentials or workload access.</p><button type="button" data-action="logout" ?disabled=${this.busy}>Sign out of this browser</button></article></section>`
  }
  private content(session: Session): TemplateResult {
    if (this.route === "discovery") return this.discoveryPage()
    if (this.route === "estimate") return this.estimatePage()
    if (this.route === "workloads") return this.workloadsPage()
    if (this.route === "credentials") return this.credentialsPage()
    if (this.route === "usage") return this.usagePage()
    if (this.route === "profile") return this.profile(session)
    return this.overview(session)
  }
  protected updated(changed: PropertyValues): void {
    if (changed.has("secret") && this.secret && this.secretDialog && !this.secretDialog.open) this.secretDialog.showModal()
  }
  protected render(): TemplateResult {
    const metadata = routeMetadata(this.route)
    const session = this.auth.tag === "in" ? this.auth.session : undefined
    return html`<och-app-shell @click=${this.action} .heading=${session ? metadata.label : "Clearinghouse account"} .summary=${session ? metadata.summary : "Discover, authorize, and measure Livepeer work."} exportparts="skip-link, shell, sidebar, header, sidebar-header, brand, brand-name, brand-accent, brand-subtitle, sidebar-close-button, sidebar-close-icon, sidebar-close-label, navigation, navigation-backdrop, workspace, topbar, menu-button, menu-icon, menu-label, mobile-brand, context, utility, main, heading-group, title, summary, content, footer, footer-note">
      ${session ? html`<p slot="navigation" part="navigation-heading">Account</p><ul slot="navigation" part="navigation-list">${navigation.map((item) => html`<li><a part=${this.route === item.route ? "navigation-link navigation-link-active" : "navigation-link"} href=${item.href} aria-current=${this.route === item.route ? "page" : "false"} @click=${this.navigate}>${icon(item.icon)}<span part="navigation-label">${item.label}</span></a></li>`)}</ul>` : nothing}
      <p slot="context" part="account-context">${session?.email ?? "User console"}</p>
      <och-denomination-control slot="utility" exportparts="field: denomination-field, label: denomination-label, select: denomination-select"></och-denomination-control>
      ${this.notice ? html`<p part=${this.notice.error ? "notice notice-danger" : "notice notice-success"} role=${this.notice.error ? "alert" : "status"}>${this.notice.text}</p>` : nothing}
      ${this.auth.tag === "checking" ? html`<p role="status">Checking your session…</p>` : nothing}
      ${this.auth.tag === "out" ? this.signIn() : nothing}${session ? this.content(session) : nothing}
      ${this.secret ? html`<dialog part="dialog" aria-labelledby="secret" aria-describedby="secret-guidance"><h2 id="secret">${this.secret.heading}</h2><div id="secret-guidance"><p>${this.secret.guidance}</p><p><strong>Save this value now.</strong> It cannot be shown again.</p></div><code>${this.secret.value}</code><button type="button" data-action="close-secret">Close after saving this secret</button></dialog>` : nothing}
    </och-app-shell>`
  }
}
declare global { interface HTMLElementTagNameMap { "user-app": UserApp } }
