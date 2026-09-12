import {
  CursorPaginationController,
  formatAmount,
  readDisplayDenomination,
  subscribeDisplayDenomination,
  type DisplayDenomination
} from "@livepeer/clearinghouse-ui"
import { Effect } from "effect"
import { LitElement, css, html, nothing, svg, type PropertyValues, type TemplateResult } from "lit"
import { customElement, query, state } from "lit/decorators.js"
import { ApiFailure, forkUser, UserApi, type UserFailure } from "./api.js"
import type { Cost, Credential, Offer, Provider, Session, Summary, Workload } from "./contracts.js"
import { navigation, routeFromPath, routeMetadata, type UserIcon, type UserRoute } from "./navigation.js"
import { estimateQuote, type QuoteInputs } from "./quote-estimator.js"

type Auth = { readonly tag: "checking" | "out" } | { readonly tag: "in"; readonly session: Session }
type Notice = { readonly error: boolean; readonly text: string }
type Secret = { readonly heading: string; readonly value: string }
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
  @state() private notice?: Notice
  @state() private secret: Secret | undefined
  @state() private busy = false
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
    if (failure instanceof ApiFailure && failure.status === 401) this.auth = { tag: "out" }
    else if (failure instanceof ApiFailure && failure.status === 409) {
      this.pagination.reset()
      this.notice = { error: true, text: "Network offers changed. Pagination restarted at page one." }
      this.loadRoute()
      return
    }
    else if (this.auth.tag === "checking") this.auth = { tag: "out" }
    this.notice = { error: true, text: "The clearinghouse could not complete that request." }
  }
  private loadIdentity(): void {
    this.run(Effect.gen(function* () {
      const api = yield* UserApi
      return { providers: yield* api.providers(), session: yield* api.session() }
    }), ({ providers, session }) => {
      this.providers = providers.providers
      this.auth = { tag: "in", session }
      this.loadRoute()
    })
  }
  private loadRoute(): void {
    if (this.auth.tag !== "in") return
    const route = this.route
    const load = ++this.routeLoad
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
    }, () => undefined, () => load === this.routeLoad)
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
        value: yield* api.createWorkload(String(values.get("offer") ?? ""), String(values.get("reference") ?? ""))
      }
    })
    this.run(effect, (result) => {
      if (result.tag === "verify") this.auth = { tag: "in", session: result.value }
      if (result.tag === "credential") this.secret = { heading: "API credential created", value: result.value.token }
      if (result.tag === "workload") this.secret = { heading: "Python SDK token created", value: result.value.sdk_token }
      this.notice = { error: false, text: kind === "request" ? "A one-time code has been sent." : "Saved." }
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
      this.run(Effect.flatMap(UserApi, (api) => api.logout()), () => { this.auth = { tag: "out" } }, () => { this.busy = false })
      return
    }
    if (!id) return
    this.busy = true
    const effect = Effect.flatMap(UserApi, (api) => action === "revoke-credential"
      ? api.revokeCredential(id) : api.revokeWorkload(id))
    this.run(effect, () => this.loadRoute(), () => { this.busy = false })
  }
  private signIn(): TemplateResult {
    return html`<section part="page auth-page" aria-labelledby="sign-in"><header part="page-header"><div><p part="eyebrow">Secure account access</p><h2 id="sign-in">Sign in</h2></div></header>
      <div part="auth-grid"><article part="surface"><h3>Email a one-time code</h3><form @submit=${this.requestCode}><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" required><button ?disabled=${this.busy}>Send one-time code</button></form></article>
      <article part="surface"><h3>Verify your code</h3><form @submit=${this.verifyCode}><label for="verify-email">Email address</label><input id="verify-email" name="email" type="email" autocomplete="email" required><label for="code">Six-digit code</label><input id="code" name="code" inputmode="numeric" .autocomplete=${"one-time-code"} pattern="[0-9]{6}" required><button ?disabled=${this.busy}>Verify and sign in</button></form></article></div>
      ${this.providers.length > 1 ? html`<nav part="surface" aria-label="Other sign-in options"><h3>Other sign-in options</h3><ul>${this.providers.filter((provider) => provider !== "email").map((provider) => html`<li><a href=${`/v1/auth/oauth/${provider}/start`}>Continue with ${provider}</a></li>`)}</ul></nav>` : nothing}</section>`
  }
  private overview(session: Session): TemplateResult {
    const summary = this.summary
    return html`<section part="page" aria-labelledby="overview"><header part="page-header"><div><p part="eyebrow">Account workspace</p><h2 id="overview">Clearinghouse overview</h2></div><strong part="badge badge-success">Active</strong></header>
      <article part="surface"><h3>${session.email}</h3><p><code>${session.account_id}</code></p></article><dl part="metrics"><div part="surface metric"><dt>Network offers</dt><dd>${summary?.offers ?? 0}</dd></div><div part="surface metric"><dt>Active workloads</dt><dd>${summary?.active_workloads ?? 0}</dd></div><div part="surface metric"><dt>Signer-reported cost</dt><dd>${this.money(summary?.computed_fee ?? "0", 1n, summary?.currency ?? "wei")}</dd></div></dl></section>`
  }
  private discoveryPage(): TemplateResult {
    return html`<section part="page" aria-labelledby="network"><header part="page-header"><div><p part="eyebrow">Livepeer network</p><h2 id="network">Capabilities and prices</h2></div><strong part="badge badge-info">${this.offers.length} on this page</strong></header><search><form part="surface" @submit=${this.filterOffers}><label for="capability-filter">Capability</label><input id="capability-filter" name="capability" .value=${this.capabilityFilter}><label for="model-filter">Model</label><input id="model-filter" name="model" .value=${this.modelFilter}><button>Apply filters</button></form></search><div part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="Network offers"><table><caption>Currently advertised runner capabilities and exact prices</caption><thead><tr><th scope="col">Capability</th><th scope="col">Model</th><th scope="col">Runner</th><th scope="col">Price</th></tr></thead><tbody>${this.offers.map((offer) => html`<tr><th scope="row">${offer.capability}</th><td>${offer.model ?? "Any"}</td><td><code>${offer.runner_url}</code></td><td>${this.money(offer.price.numerator, offer.price.denominator, offer.price.currency)} per ${offer.price.quantity_unit}</td></tr>`)}</tbody></table></div></div>${this.paginationControl()}</section>`
  }
  private estimateFields(unit: string): TemplateResult {
    const durationLabel = unit === "hour" || unit === "hours" ? "Expected runtime in hours" : "Expected runtime in seconds"
    const duration = html`<label for="duration">${durationLabel}</label><input id="duration" name="duration" type="number" min="0.000000001" step="0.000000001" .value=${this.quoteInputs.duration} @input=${this.updateQuoteInput} required>`
    const dimensions = html`<label for="width">Output width in pixels</label><input id="width" name="width" type="number" min="1" step="1" .value=${this.quoteInputs.width} @input=${this.updateQuoteInput} required><label for="height">Output height in pixels</label><input id="height" name="height" type="number" min="1" step="1" .value=${this.quoteInputs.height} @input=${this.updateQuoteInput} required>`
    if (["second", "seconds", "hour", "hours"].includes(unit)) return duration
    if (["pixel", "pixels"].includes(unit)) return html`${dimensions}<label for="frames">Frames or images per execution</label><input id="frames" name="frames" type="number" min="1" step="1" .value=${this.quoteInputs.frames} @input=${this.updateQuoteInput} required>`
    if (unit === "720p-pixel-seconds") return html`${dimensions}<label for="fps">Frames per second</label><input id="fps" name="fps" type="number" min="1" step="1" .value=${this.quoteInputs.fps} @input=${this.updateQuoteInput} required>${duration}`
    return html`<p part="hint">This offer does not publish a billing unit the estimator understands.</p>`
  }
  private estimatePage(): TemplateResult {
    const selected = this.offers.find((offer) => offer.id === this.estimateOfferId) ?? this.offers[0]
    if (selected === undefined) return html`<section part="page" aria-labelledby="estimate"><header part="page-header"><div><p part="eyebrow">Plan before authorizing</p><h2 id="estimate">Cost estimator</h2></div></header><search><form part="surface" @submit=${this.filterOffers}><label for="empty-capability-filter">Capability</label><input id="empty-capability-filter" name="capability" .value=${this.capabilityFilter}><label for="empty-model-filter">Model</label><input id="empty-model-filter" name="model" .value=${this.modelFilter}><button>Find offers</button></form></search><article part="surface"><h3>No priced offers available</h3><p>The estimator needs a current network offer with an exact advertised rate.</p></article>${this.paginationControl()}</section>`
    const unit = selected.price.quantity_unit.trim().toLowerCase()
    const estimate = estimateQuote(selected.price, this.quoteInputs)
    return html`<section part="page" aria-labelledby="estimate"><header part="page-header"><div><p part="eyebrow">Plan before authorizing</p><h2 id="estimate">Cost estimator</h2></div><strong part="badge badge-info">Exact rate arithmetic</strong></header><search><form part="surface" @submit=${this.filterOffers}><label for="estimate-capability-filter">Capability</label><input id="estimate-capability-filter" name="capability" .value=${this.capabilityFilter}><label for="estimate-model-filter">Model</label><input id="estimate-model-filter" name="model" .value=${this.modelFilter}><button>Find offers</button></form></search>
      <div part="estimator-grid"><form part="form-card" @submit=${this.createWorkload}><fieldset><legend>Advertised offer</legend><label for="estimate-offer">Capability and provider</label><select id="estimate-offer" name="offer" .value=${selected.id} @change=${this.chooseEstimateOffer} required>${this.offers.map((offer) => html`<option value=${offer.id}>${offer.capability}${offer.model ? ` / ${offer.model}` : ""} — ${rate(offer, this.denomination)}</option>`)}</select><p id="offer-help" part="hint"><code>${selected.runner_url}</code></p></fieldset>
        <fieldset><legend>Usage assumptions</legend><div part="field-grid"><label for="executions">Number of executions</label><input id="executions" name="executions" type="number" min="1" step="1" .value=${this.quoteInputs.executions} @input=${this.updateQuoteInput} required>${this.estimateFields(unit)}</div></fieldset>
        <label for="estimate-reference">Your job reference (optional)</label><input id="estimate-reference" name="reference" maxlength="256" autocomplete="off"><button ?disabled=${this.busy || estimate._tag !== "Ready"}>Create Python SDK token at this rate</button></form>
        <article part="surface estimate-result" aria-labelledby="estimate-result"><h3 id="estimate-result">Estimated quote</h3>${estimate._tag === "Ready" ? html`<dl part="estimate-summary"><div part="estimate-row"><dt part="estimate-term">Advertised rate</dt><dd part="estimate-value">${this.money(selected.price.numerator, selected.price.denominator, selected.price.currency)} per ${selected.price.quantity_unit}</dd></div><div part="estimate-row"><dt part="estimate-term">Estimated quantity</dt><dd part="estimate-value"><data value=${estimate.quantity}>${estimate.quantity}</data> ${estimate.quantityUnit}${estimate.quantity === "1" ? "" : "s"}</dd></div><div part="estimate-row"><dt part="estimate-term">Estimated cost</dt><dd part="estimate-value"><output for="estimate-offer executions duration width height frames fps">${this.money(estimate.cost, 1n, selected.price.currency)}</output></dd></div><div part="estimate-row"><dt part="estimate-term">Offer expires</dt><dd part="estimate-value"><time datetime=${selected.expires_at}>${date(selected.expires_at)}</time></dd></div></dl>` : estimate._tag === "Invalid" ? html`<p part="notice notice-danger" role="status">${estimate.message}</p>` : html`<p part="notice notice-danger" role="status">Billing unit <code>${estimate.unit}</code> is not supported by this estimator.</p>`}<p part="hint"><small>This is an estimate from your assumptions and the selected advertised rate. The workload snapshots that rate; final cost comes from signer-measured usage and may differ.</small></p></article></div>${this.paginationControl()}</section>`
  }
  private workloadsPage(): TemplateResult {
    return html`<section part="page" aria-labelledby="workloads"><header part="page-header"><div><p part="eyebrow">Quoted access</p><h2 id="workloads">Workloads</h2></div></header><article part="form-card"><h3>Create workload access</h3><form @submit=${this.createWorkload}><label for="offer">Advertised offer</label><select id="offer" name="offer" required>${this.offers.map((offer) => html`<option value=${offer.id}>${offer.capability} — ${rate(offer, this.denomination)}</option>`)}</select><label for="reference">Your job reference</label><input id="reference" name="reference" maxlength="256"><button ?disabled=${this.busy}>Create Python SDK token</button></form></article><div part="card-grid">${this.workloads.map((workload) => html`<article part="surface"><h3>${workload.capability}</h3><p><code>${workload.id}</code></p><p>${workload.client_reference ?? "No client reference"}</p><p>Access ends <time datetime=${workload.expires_at}>${date(workload.expires_at)}</time></p><strong part=${workload.status === "active" ? "badge badge-success" : "badge"}>${statusLabel(workload.status)}</strong>${workload.status === "active" ? html`<button data-action="revoke-workload" data-id=${workload.id} ?disabled=${this.busy}>Revoke</button>` : nothing}</article>`)}</div>${this.paginationControl()}</section>`
  }
  private credentialsPage(): TemplateResult {
    return html`<section part="page" aria-labelledby="credentials"><header part="page-header"><div><p part="eyebrow">Programmatic access</p><h2 id="credentials">API credentials</h2></div></header><article part="form-card"><h3>Create credential</h3><form @submit=${this.createCredential}><label for="name">Credential name</label><input id="name" name="name" maxlength="80" required><button ?disabled=${this.busy}>Create credential</button></form></article><div part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="API credentials"><table><caption>Credentials for this account</caption><thead><tr><th scope="col">Name</th><th scope="col">Created</th><th scope="col">Action</th></tr></thead><tbody>${this.credentials.map((credential) => html`<tr><th scope="row">${credential.name}</th><td><time datetime=${credential.created_at}>${date(credential.created_at)}</time></td><td><button data-action="revoke-credential" data-id=${credential.id} ?disabled=${this.busy}>Revoke</button></td></tr>`)}</tbody></table></div></div>${this.paginationControl()}</section>`
  }
  private usagePage(): TemplateResult {
    return html`<section part="page" aria-labelledby="usage"><header part="page-header"><div><p part="eyebrow">Measured economics</p><h2 id="usage">Usage and cost</h2></div></header><div part="table-panel"><div part="table-scroll" tabindex="0" role="region" aria-label="Workload costs"><table><caption>Cost by workload at its observed price</caption><thead><tr><th scope="col">Workload</th><th scope="col">Quantity</th><th scope="col">Quoted</th><th scope="col">Signer fee</th></tr></thead><tbody>${this.costs.map((cost) => html`<tr><th scope="row"><code>${cost.workload.id}</code></th><td>${cost.measured_quantity} ${cost.measured_unit}</td><td>${this.money(cost.quoted_fee, 1n, cost.currency)}</td><td>${this.money(cost.computed_fee, 1n, cost.currency)}</td></tr>`)}</tbody></table></div></div>${this.paginationControl()}<p>${this.summary?.usage_events ?? 0} signer events attributed.</p></section>`
  }
  private profile(session: Session): TemplateResult {
    return html`<section part="page" aria-labelledby="profile"><header part="page-header"><div><p part="eyebrow">Identity</p><h2 id="profile">Profile and security</h2></div></header><article part="surface"><dl><div><dt>Email</dt><dd>${session.email}</dd></div><div><dt>User</dt><dd><code>${session.user_id}</code></dd></div><div><dt>Account</dt><dd><code>${session.account_id}</code></dd></div></dl><button data-action="logout" ?disabled=${this.busy}>Sign out</button></article></section>`
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
      ${this.secret ? html`<dialog part="dialog" aria-labelledby="secret"><h2 id="secret">${this.secret.heading}</h2><p>Save this value now. It cannot be shown again.</p><code>${this.secret.value}</code><button data-action="close-secret">I saved it</button></dialog>` : nothing}
    </och-app-shell>`
  }
}
declare global { interface HTMLElementTagNameMap { "user-app": UserApp } }
