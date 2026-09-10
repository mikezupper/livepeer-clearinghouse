import "@livepeer/clearinghouse-ui"
import { msg } from "@lit/localize"
import { DateTime, Effect, Either } from "effect"
import { LitElement, css, html, nothing, type TemplateResult } from "lit"
import { customElement, property, query, state } from "lit/decorators.js"
import { AdminApiLive, applyMutation, loadOverview, reasonFromFailure } from "./api.js"
import { ApiProblem, ResourceState, type AdminFailure, type AuthSession, type Overview } from "./domain.js"
import { provideAdminApi, runAdmin } from "./runtime.js"

const field = (form: FormData, name: string): string => {
  const value = form.get(name)
  return typeof value === "string" ? value.trim() : ""
}
const instant = (value: DateTime.Utc): string => DateTime.formatIso(value)
const localInstant = (value: string): string | null => {
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null
}

interface PendingChange {
  readonly path: string
  readonly method: "POST" | "PUT" | "PATCH" | "DELETE"
  readonly body: Readonly<Record<string, unknown>>
  readonly idempotencyKey?: string
  readonly target: string
  readonly action: string
  readonly onSaved?: () => void
}

@customElement("admin-app")
export class AdminApp extends LitElement {
  static styles = css`
    :host { display: block; }
    [part~="section"] { margin-block-start: var(--och-space-4); }
    [part~="metrics"] { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(11rem, 100%), 1fr)); gap: var(--och-space-2); }
    [part~="metric"], [part~="form-card"] { padding: var(--och-space-3); background: var(--och-color-surface-raised); border: var(--admin-border-width) solid var(--admin-card-border); border-radius: var(--och-radius); box-shadow: var(--och-shadow); }
    [part~="metric"] dt { color: var(--och-color-text-muted); }
    [part~="metric"] dd { margin-inline-start: 0; font-size: var(--och-font-size-1); font-weight: var(--admin-metric-weight); }
    [part~="controls"] { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(18rem, 100%), 1fr)); gap: var(--och-space-3); }
    [part~="controls"] > :is(h2, p) { grid-column: 1 / -1; }
    [part~="table-scroll"] { overflow-inline: auto; }
    table { inline-size: 100%; border-collapse: collapse; }
    :is(th, td) { padding: var(--och-space-2); border-block-end: 1px solid var(--och-color-border); text-align: start; }
    caption { padding-block: var(--och-space-2); color: var(--och-color-text-muted); text-align: start; }
    form { display: grid; gap: var(--och-space-2); }
    :is(input, select, textarea, button) { min-block-size: var(--admin-control-size, 2.75rem); padding: var(--och-space-1, 0.5rem) var(--och-space-2, 0.75rem); }
    a { display: inline-flex; align-items: center; min-block-size: var(--admin-control-size, 2.75rem); padding-inline: var(--och-space-1, 0.5rem); }
    textarea { min-block-size: var(--admin-textarea-size, 5lh); }
    button { transition: opacity var(--admin-motion-duration) ease; }
    menu { display: flex; justify-content: end; gap: var(--och-space-2); padding: 0; list-style: none; }
  `
  @property({ attribute: false }) api = AdminApiLive
  @state() private resource: ResourceState = ResourceState.Loading()
  @state() private message = ""
  @state() private saving = false
  @state() private proposedStop = false
  @state() private providers: ReadonlyArray<"email" | "google" | "github"> = ["email"]
  @state() private emailAddress = ""
  @state() private codeRequested = false
  @state() private invitationSecret: string | null = null
  @state() private pendingChange: PendingChange | null = null
  @query("#kill-confirmation") private killDialog?: HTMLDialogElement
  @query("#change-confirmation") private changeDialog?: HTMLDialogElement
  @query("#invitation-secret") private invitationDialog?: HTMLDialogElement
  private grantKey = crypto.randomUUID()

  connectedCallback(): void {
    // LitElement always defines this callback; the guard rule targets generic elements.
    // eslint-disable-next-line wc/guard-super-call
    super.connectedCallback()
    window.addEventListener("hashchange", this.clearInvitation)
    this.refresh()
  }

  disconnectedCallback(): void {
    window.removeEventListener("hashchange", this.clearInvitation)
    this.clearInvitation()
    super.disconnectedCallback?.()
  }

  private readonly clearInvitation = (): void => {
    this.invitationSecret = null
  }

  refresh(): void {
    this.resource = ResourceState.Loading()
    void runAdmin(provideAdminApi(this.api, loadOverview)).then(Either.match({
      onLeft: (failure) => {
        const status = failure instanceof ApiProblem ? failure.status : null
        this.resource = ResourceState.Failed({ message: reasonFromFailure(failure), status })
        if (status === 401) this.loadProviders()
      },
      onRight: (value) => { this.resource = ResourceState.Ready({ value }) }
    }))
  }

  private authAction(effect: Effect.Effect<void, AdminFailure>, onSuccess: () => void): void {
    if (this.saving) return
    this.saving = true
    this.message = ""
    void runAdmin(effect).then(Either.match({
      onLeft: (failure) => { this.saving = false; this.message = reasonFromFailure(failure) },
      onRight: () => { this.saving = false; onSuccess() }
    }))
  }

  private loadProviders(): void {
    void runAdmin(this.api.providers).then(Either.match({
      onLeft: () => { this.providers = ["email"] },
      onRight: (providers) => { this.providers = providers }
    }))
  }

  private requestEmailCode(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    this.emailAddress = field(new FormData(event.currentTarget), "email")
    this.authAction(this.api.requestEmailCode(this.emailAddress), () => {
      this.codeRequested = true
      this.message = "If the address is eligible, a one-time code has been sent."
    })
  }

  private verifyEmailCode(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const code = field(new FormData(event.currentTarget), "code")
    this.authAction(this.api.verifyEmailCode(this.emailAddress, code), () => { this.refresh() })
  }

  private refreshSession(): void {
    this.authAction(this.api.refreshSession, () => {
      this.message = "Session refreshed."
      this.refresh()
    })
  }

  private logout(): void {
    this.authAction(this.api.logout, () => {
      this.message = "Signed out."
      this.refresh()
    })
  }

  private submit(
    path: string,
    method: "POST" | "PUT" | "PATCH" | "DELETE",
    body: Readonly<Record<string, unknown>>,
    idempotencyKey?: string,
    onSaved?: () => void
  ): void {
    if (this.saving) return
    this.saving = true
    this.message = ""
    const command = idempotencyKey === undefined ? { path, method, body } as const
      : { path, method, body, idempotencyKey } as const
    void runAdmin(provideAdminApi(this.api, applyMutation(command))).then(Either.match({
      onLeft: (failure) => { this.saving = false; this.message = reasonFromFailure(failure) },
      onRight: () => {
        onSaved?.()
        this.saving = false
        this.message = "Change saved and authoritative data refreshed."
        this.refresh()
      }
    }))
  }

  private createTenant(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    this.submit("/v1/tenants", "POST", { display_name: field(new FormData(event.currentTarget), "display_name") })
  }

  private createAccount(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    this.submit("/v1/accounts", "POST", { tenant_id: field(form, "tenant_id"), display_name: field(form, "display_name"), unit: field(form, "unit"), exposure_cap: field(form, "exposure_cap") })
  }

  private createPrincipal(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const tenant = field(form, "tenant_id")
    const account = field(form, "account_id")
    this.submit("/v1/principals", "POST", {
      tenant_id: tenant || null,
      account_id: account || null,
      display_name: field(form, "display_name") || null,
      roles: [field(form, "role")]
    })
  }

  private createGrant(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const account = field(form, "account_id")
    this.confirmChange({
      path: "/v1/grants",
      method: "POST",
      body: { account_id: account, kind: field(form, "kind"), amount: { amount: field(form, "amount"), unit: field(form, "unit") }, reason: field(form, "reason"), external_reference: field(form, "external_reference") || null },
      idempotencyKey: this.grantKey,
      target: account,
      action: "Post an immutable ledger grant",
      onSaved: () => { this.grantKey = crypto.randomUUID() }
    })
  }

  private createRate(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const capability = field(form, "capability")
    const effectiveAt = localInstant(field(form, "effective_at"))
    if (effectiveAt === null) {
      this.message = "Enter a valid effective time."
      return
    }
    this.confirmChange({
      path: "/v1/rate-cards",
      method: "POST",
      body: { capability, model: field(form, "model") || null, rate: { numerator: field(form, "numerator"), denominator: field(form, "denominator"), charge_unit: field(form, "charge_unit"), quantity_unit: field(form, "quantity_unit") }, effective_at: effectiveAt },
      target: capability,
      action: "Publish an immutable rate-card version"
    })
  }

  private updateTenant(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const id = field(form, "tenant_id")
    const status = field(form, "status")
    this.confirmChange({ path: `/v1/tenants/${id}`, method: "PATCH", body: { status, reason: field(form, "reason") }, target: id, action: `${status} tenant and its authorization scope` })
  }

  private updateAccount(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const id = field(form, "account_id")
    this.confirmChange({ path: `/v1/accounts/${id}`, method: "PATCH", body: { exposure_cap: field(form, "exposure_cap"), status: field(form, "status"), reason: field(form, "reason") }, target: id, action: "Change payer account cap or status" })
  }

  private updatePrincipal(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const id = field(form, "principal_id")
    const role = field(form, "role")
    this.confirmChange({ path: `/v1/principals/${id}`, method: "PATCH", body: { status: field(form, "status"), roles: [role], reason: field(form, "reason") }, target: id, action: "Change principal role or status" })
  }

  private setCapability(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const id = field(form, "account_id")
    this.confirmChange({ path: `/v1/accounts/${id}/capabilities`, method: "PUT", body: { capability: field(form, "capability"), model: field(form, "model") || null, allowed: field(form, "allowed") === "true", reason: field(form, "reason") }, target: id, action: "Change account capability policy" })
  }

  private issueInvitation(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    if (this.saving) return
    this.saving = true
    this.invitationSecret = null
    void runAdmin(this.api.issueInvitation(field(form, "principal_id"), field(form, "source_principal_id"), field(form, "reason"))).then(Either.match({
      onLeft: (failure) => { this.saving = false; this.message = reasonFromFailure(failure) },
      onRight: (secret) => {
        this.saving = false
        this.invitationSecret = secret
        this.message = "Invitation issued. Copy the one-time secret now."
        void this.updateComplete.then(() => {
          this.invitationDialog?.showModal()
          this.invitationDialog?.querySelector<HTMLButtonElement>("button")?.focus()
        })
      }
    }))
  }

  private confirmChange(change: PendingChange): void {
    this.pendingChange = change
    this.changeDialog?.showModal()
    this.changeDialog?.querySelector<HTMLButtonElement>("button[value='cancel']")?.focus()
  }

  private cancelChange(): void {
    this.pendingChange = null
    this.changeDialog?.close()
  }

  private applyConfirmedChange(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const change = this.pendingChange
    this.pendingChange = null
    this.changeDialog?.close()
    if (change !== null) {
      this.submit(change.path, change.method, change.body, change.idempotencyKey, change.onSaved)
    }
  }

  private openKillDialog(event: Event): void {
    this.proposedStop = event.currentTarget instanceof HTMLButtonElement
      && event.currentTarget.value === "stop"
    this.killDialog?.showModal()
    const reason = this.killDialog?.querySelector<HTMLTextAreaElement>("textarea")
    if (reason !== null && reason !== undefined) reason.value = ""
    this.killDialog?.querySelector<HTMLButtonElement>("button[value='cancel']")?.focus()
  }

  private closeKillDialog(): void {
    const reason = this.killDialog?.querySelector<HTMLTextAreaElement>("textarea")
    if (reason !== null && reason !== undefined) reason.value = ""
    this.killDialog?.close()
  }

  private confirmKillSwitch(event: SubmitEvent): void {
    if (!(event.currentTarget instanceof HTMLFormElement)) return
    event.preventDefault()
    const reason = field(new FormData(event.currentTarget), "reason")
    this.killDialog?.close()
    this.submit("/v1/operations/kill-switch", "PUT", { enabled: this.proposedStop, reason })
  }

  private table(id: string, caption: string, headings: ReadonlyArray<string>, rows: ReadonlyArray<ReadonlyArray<unknown>>): TemplateResult {
    return html`<section id=${id} part="section table-section" aria-labelledby=${`${id}-heading`}>
      <h2 id=${`${id}-heading`}>${caption}</h2><div part="table-scroll" tabindex="0" role="region" aria-label=${`${caption} table`}><table>
        <caption>${caption} from the clearinghouse API</caption>
        <thead><tr>${headings.map((heading) => html`<th scope="col">${heading}</th>`)}</tr></thead>
        <tbody>${rows.length === 0 ? html`<tr><td colspan=${headings.length}>No records.</td></tr>`
          : rows.map((row) => html`<tr>${row.map((cell, index) => index === 0
            ? html`<th scope="row">${cell}</th>`
            : html`<td>${cell}</td>`)}</tr>`)}</tbody>
      </table></div></section>`
  }

  private renderOverview(data: Overview): TemplateResult {
    const killSwitch = data.killSwitch
    return html`
      <section id="overview" part="section overview" aria-labelledby="overview-heading"><h2 id="overview-heading">Authoritative overview</h2>
        <dl part="metrics"><div part="metric"><dt>Tenants</dt><dd>${data.tenants.length}</dd></div><div part="metric"><dt>Accounts</dt><dd>${data.accounts.length}</dd></div>
          <div part="metric"><dt>Open exposure</dt><dd>${data.metering === null ? "Operator view required" : html`<data value=${data.metering.global_open_exposure.toString()}>${data.metering.global_open_exposure.toLocaleString()} wei</data>`}</dd></div>
          <div part="metric"><dt>Exposure cap</dt><dd>${data.metering === null ? "Operator view required" : html`<data value=${data.metering.global_exposure_cap.toString()}>${data.metering.global_exposure_cap.toLocaleString()} wei</data>`}</dd></div>
          <div part="metric"><dt>Metering</dt><dd><strong>${data.metering?.status ?? "Tenant scope"}</strong></dd></div><div part="metric"><dt>Open cases</dt><dd>${data.metering?.open_cases ?? data.reconciliation.filter((item) => item.status === "open").length}</dd></div></dl>
      </section>
      ${this.table("tenants", "Tenants", ["Tenant", "Status", "Created"], data.tenants.map((item) => [html`<bdi>${item.display_name}</bdi><small><code>${item.id}</code></small>`, item.status, html`<time datetime=${instant(item.created_at)}>${instant(item.created_at)}</time>`]))}
      ${this.table("accounts", "Payer accounts", ["Account", "Tenant", "Cap", "Status"], data.accounts.map((item) => [html`<bdi>${item.display_name}</bdi><small><code>${item.id}</code></small>`, html`<code>${item.tenant_id}</code>`, `${item.exposure_cap.toLocaleString()} ${item.unit}`, item.status]))}
      ${this.table("principals", "Principals", ["Principal", "Scope", "Roles", "Status"], data.principals.map((item) => [html`<bdi>${item.display_name ?? "Unnamed principal"}</bdi><small><code>${item.id}</code></small>`, item.account_id ?? item.tenant_id ?? "Unscoped", item.roles.join(", ") || "No roles", item.status]))}
      ${this.table("balances", "Authoritative balances", ["Account", "Posted", "Open exposure", "Available"], data.balances.map((item) => [html`<code>${item.account_id}</code>`, `${item.posted.amount.toLocaleString()} ${item.posted.unit}`, `${item.open_lease_exposure.amount.toLocaleString()} ${item.open_lease_exposure.unit}`, `${item.available.amount.toLocaleString()} ${item.available.unit}`]))}
      ${this.table("grants", "Ledger grants", ["Account", "Kind", "Amount", "Reason"], data.grants.map((item) => [html`<code>${item.account_id}</code>`, item.kind, `${item.amount.amount.toLocaleString()} ${item.amount.unit}`, html`<bdi>${item.reason}</bdi>`]))}
      ${this.table("leases", "Leases", ["Lease", "Available", "Pending", "Settled", "Expires"], data.leases.map((item) => [html`<code>${item.id}</code>`, `${item.available.toLocaleString()} ${item.unit}`, `${item.pending.toLocaleString()} ${item.unit}`, `${item.settled.toLocaleString()} ${item.unit}`, html`<time datetime=${instant(item.expires_at)}>${instant(item.expires_at)}</time>`]))}
      ${this.table("reservations", "Open reservations", ["Reservation", "Status", "Reserved", "Sequence"], data.reservations.map((item) => [html`<code>${item.id}</code>`, item.status, `${item.reserved_amount.value.toLocaleString()} ${item.reserved_amount.unit}`, item.sequence_number.toLocaleString()]))}
      ${this.table("usage", "Usage", ["Capability", "Quantity", "Account", "Occurred"], data.usage.map((item) => [item.capability, `${item.quantity.value.toLocaleString()} ${item.quantity.unit}`, html`<code>${item.account_id}</code>`, html`<time datetime=${item.occurred_at}>${item.source.signed_current_time}</time>`]))}
      ${this.table("charges", "Charges", ["Charge", "Account", "Amount", "Created"], data.charges.map((item) => [html`<code>${item.id}</code>`, html`<code>${item.account_id}</code>`, `${item.amount.value.toLocaleString()} ${item.amount.unit}`, html`<time datetime=${instant(item.created_at)}>${instant(item.created_at)}</time>`]))}
      ${this.table("rates", "Immutable rate cards", ["Capability", "Model", "Rate", "Effective"], data.rates.map((item) => [item.capability, item.model ?? "All models", `${item.rate.numerator}/${item.rate.denominator} ${item.rate.charge_unit} per ${item.rate.quantity_unit}`, html`<time datetime=${instant(item.effective_at)}>${instant(item.effective_at)}</time>`]))}
      ${this.table("reconciliation", "Metering reconciliation", ["Case", "Kind", "Status", "Created", "Resolved"], data.reconciliation.map((item) => [html`<code>${item.id}</code>`, item.kind, item.status, html`<time datetime=${instant(item.created_at)}>${instant(item.created_at)}</time>`, item.resolved_at === null ? "Open" : html`<time datetime=${instant(item.resolved_at)}>${instant(item.resolved_at)}</time>`]))}
      ${data.adapters === null ? html`<section id="adapters" part="section state"><h2>Active adapters</h2><p>An operator role is required to view the adapter inventory.</p></section>` : this.table("adapters", "Active adapters", ["Adapter", "Source", "Version", "Ports"], data.adapters.map((item) => [html`<bdi>${item.name}</bdi>`, item.source, item.version, item.ports.map((port) => port.name).join(", ")]))}
      ${this.table("audit", "Immutable audit trail", ["Action", "Target", "Reason", "Occurred"], data.audit.map((item) => [item.action, html`<bdi>${item.target_id}</bdi>`, html`<bdi>${item.reason}</bdi>`, html`<time datetime=${instant(item.occurred_at)}>${instant(item.occurred_at)}</time>`]))}
      ${this.renderControls(data.session)}
      ${killSwitch === null ? nothing : html`<section id="emergency" part="section emergency" aria-labelledby="emergency-heading"><h2 id="emergency-heading">Emergency authorization control</h2>
        <p>Current state: <strong>${killSwitch.enabled ? "stopped" : "authorizing"}</strong>.</p>
        <button type="button" value=${killSwitch.enabled ? "resume" : "stop"} ?disabled=${this.saving} @click=${this.openKillDialog}>${killSwitch.enabled ? "Resume authorization" : "Stop authorization"}</button></section>`}`
  }

  private renderSignIn(): TemplateResult {
    return html`<section id="sign-in" part="section state" aria-labelledby="sign-in-heading"><h2 id="sign-in-heading">${msg("Sign in to administer the clearinghouse")}</h2>
      ${this.providers.includes("email") ? html`${this.codeRequested
        ? html`<form @submit=${this.verifyEmailCode}><p>A six-digit code was requested for <bdi>${this.emailAddress}</bdi>.</p><label for="email-code">One-time code</label><input id="email-code" name="code" inputmode="numeric" .autocomplete=${"one-time-code"} pattern="[0-9]{6}" required><button type="submit" ?disabled=${this.saving}>Verify code</button></form>`
        : html`<form @submit=${this.requestEmailCode}><label for="sign-in-email">Email address</label><input id="sign-in-email" name="email" type="email" autocomplete="email" maxlength="320" required><button type="submit" ?disabled=${this.saving}>Email a one-time code</button></form>`}` : nothing}
      ${this.providers.includes("google") || this.providers.includes("github") ? html`<nav aria-label="Other sign-in options"><ul>${this.providers.includes("google") ? html`<li><a href="/v1/auth/oauth/google/start">Continue with Google</a></li>` : nothing}${this.providers.includes("github") ? html`<li><a href="/v1/auth/oauth/github/start">Continue with GitHub</a></li>` : nothing}</ul></nav>` : nothing}
    </section>`
  }

  private renderControls(session: AuthSession): TemplateResult {
    const operator = session.roles.includes("operator")
    const tenantScope = operator ? "" : session.tenant_id ?? ""
    return html`<section id="controls" part="section controls" aria-labelledby="controls-heading"><h2 id="controls-heading">Controlled changes</h2><p>Changes refresh from authoritative API state.</p>
      ${operator ? html`<article part="form-card"><h3>Create tenant</h3><form @submit=${this.createTenant}><label for="tenant-name">Display name</label><input id="tenant-name" name="display_name" required maxlength="200"><button type="submit" ?disabled=${this.saving}>Create tenant</button></form></article>` : nothing}
      <article part="form-card"><h3>Create payer account</h3><form @submit=${this.createAccount}><label for="account-tenant">Tenant ID</label><input id="account-tenant" name="tenant_id" .value=${tenantScope} ?readonly=${!operator} required><label for="account-name">Display name</label><input id="account-name" name="display_name" required maxlength="200"><label for="account-unit">Unit</label><input id="account-unit" name="unit" value="wei" required><label for="account-cap">Exposure cap</label><input id="account-cap" name="exposure_cap" inputmode="numeric" pattern="[0-9]+" required><button type="submit" ?disabled=${this.saving}>Create account</button></form></article>
      <article part="form-card"><h3>Create principal</h3><form @submit=${this.createPrincipal}><label for="principal-name">Display name</label><input id="principal-name" name="display_name" maxlength="200"><label for="principal-tenant">Tenant ID</label><input id="principal-tenant" name="tenant_id" .value=${tenantScope} ?readonly=${!operator}><label for="principal-account">Account ID (optional)</label><input id="principal-account" name="account_id"><label for="principal-role">Role</label><select id="principal-role" name="role"><option>credential_holder</option><option>tenant_admin</option>${operator ? html`<option>operator</option>` : nothing}</select><button type="submit" ?disabled=${this.saving}>Create principal</button></form></article>
      <article part="form-card"><h3>Post ledger grant</h3><form @submit=${this.createGrant}><label for="grant-account">Account ID</label><input id="grant-account" name="account_id" required><label for="grant-kind">Grant kind</label><select id="grant-kind" name="kind"><option>credit</option><option>debit</option><option>adjustment</option></select><label for="grant-amount">Signed amount</label><input id="grant-amount" name="amount" inputmode="numeric" pattern="-?[0-9]+" required><label for="grant-unit">Unit</label><input id="grant-unit" name="unit" value="wei" required><label for="grant-reason">Audit reason</label><textarea id="grant-reason" name="reason" required maxlength="1000"></textarea><label for="grant-reference">External reference (optional)</label><input id="grant-reference" name="external_reference" maxlength="256"><button type="submit" ?disabled=${this.saving}>Post grant</button></form></article>
      ${operator ? html`<article part="form-card"><h3>Publish rate card</h3><form @submit=${this.createRate}><label for="rate-capability">Capability</label><input id="rate-capability" name="capability" required maxlength="128"><label for="rate-model">Model (optional)</label><input id="rate-model" name="model" maxlength="512"><label for="rate-numerator">Numerator</label><input id="rate-numerator" name="numerator" inputmode="numeric" pattern="[0-9]+" required><label for="rate-denominator">Denominator</label><input id="rate-denominator" name="denominator" inputmode="numeric" pattern="[1-9][0-9]*" required><label for="rate-charge-unit">Charge unit</label><input id="rate-charge-unit" name="charge_unit" value="wei" required><label for="rate-quantity-unit">Quantity unit</label><select id="rate-quantity-unit" name="quantity_unit"><option>fixed</option><option>seconds</option><option>720p-pixel-seconds</option></select><label for="rate-effective">Effective time</label><input id="rate-effective" name="effective_at" type="datetime-local" required><button type="submit" ?disabled=${this.saving}>Publish immutable rate</button></form></article>` : nothing}
      ${operator ? html`<article part="form-card"><h3>Update tenant status</h3><form @submit=${this.updateTenant}><label for="update-tenant-id">Tenant ID</label><input id="update-tenant-id" name="tenant_id" required><label for="tenant-status">Status</label><select id="tenant-status" name="status"><option>active</option><option>suspended</option></select><label for="tenant-reason">Audit reason</label><textarea id="tenant-reason" name="reason" required maxlength="1000"></textarea><button type="submit">Review tenant change</button></form></article>` : nothing}
      <article part="form-card"><h3>Update account cap or status</h3><form @submit=${this.updateAccount}><label for="update-account-id">Account ID</label><input id="update-account-id" name="account_id" required><label for="update-account-cap">Exposure cap</label><input id="update-account-cap" name="exposure_cap" inputmode="numeric" pattern="[0-9]+" required><label for="account-status">Status</label><select id="account-status" name="status"><option>active</option><option>suspended</option></select><label for="account-reason">Audit reason</label><textarea id="account-reason" name="reason" required maxlength="1000"></textarea><button type="submit">Review account change</button></form></article>
      <article part="form-card"><h3>Update principal</h3><form @submit=${this.updatePrincipal}><label for="update-principal-id">Principal ID</label><input id="update-principal-id" name="principal_id" required><label for="update-principal-role">Role</label><select id="update-principal-role" name="role"><option>credential_holder</option><option>tenant_admin</option>${operator ? html`<option>operator</option>` : nothing}</select><label for="principal-status">Status</label><select id="principal-status" name="status"><option>active</option><option>suspended</option></select><label for="principal-reason">Audit reason</label><textarea id="principal-reason" name="reason" required maxlength="1000"></textarea><button type="submit">Review principal change</button></form></article>
      <article part="form-card"><h3>Set capability policy</h3><form @submit=${this.setCapability}><label for="policy-account">Account ID</label><input id="policy-account" name="account_id" required><label for="policy-capability">Capability</label><input id="policy-capability" name="capability" required maxlength="128"><label for="policy-model">Model (optional)</label><input id="policy-model" name="model" maxlength="512"><label for="policy-allowed">Decision</label><select id="policy-allowed" name="allowed"><option value="true">Allow</option><option value="false">Deny</option></select><label for="policy-reason">Audit reason</label><textarea id="policy-reason" name="reason" required maxlength="1000"></textarea><button type="submit">Review policy change</button></form></article>
      <article part="form-card"><h3>Issue identity invitation</h3><form @submit=${this.issueInvitation}><label for="invite-principal">Target principal ID</label><input id="invite-principal" name="principal_id" required><label for="invite-source">Verified source principal ID</label><input id="invite-source" name="source_principal_id" required><label for="invite-reason">Audit reason</label><textarea id="invite-reason" name="reason" required maxlength="1000"></textarea><button type="submit">Issue one-time invitation</button></form></article>
    </section>`
  }

  protected render(): TemplateResult {
    return html`<och-app-shell .heading=${msg("Clearinghouse administration")} .summary=${msg("Operate accountable walletless Livepeer network spend.")} exportparts="skip-link, header, brand, navigation, main, heading-group, title, summary, footer, footer-note">
      <ul slot="navigation" part="navigation-list"><li><a href="#overview">Overview</a></li><li><a href="#accounts">Accounts</a></li><li><a href="#reconciliation">Operations</a></li><li><a href="#controls">Controls</a></li></ul>
      <p part="mutation-status" aria-live="polite">${this.message || nothing}</p>
      ${ResourceState.$match({ Loading: () => html`<section part="state"><h2>Loading authoritative data</h2><progress aria-label="Loading administration data"></progress></section>`, Failed: ({ message, status }) => status === 401 ? this.renderSignIn() : html`<section part="state"><h2>Administration data is unavailable</h2><p role="alert">${message}</p><button type="button" @click=${this.refresh}>Retry</button></section>`, Ready: ({ value }) => html`<nav part="session-actions" aria-label="Session"><button type="button" @click=${this.refreshSession} ?disabled=${this.saving}>Refresh session</button><button type="button" @click=${this.logout} ?disabled=${this.saving}>Sign out</button></nav>${this.renderOverview(value)}` })(this.resource)}
      <dialog id="kill-confirmation" part="dialog" aria-labelledby="kill-title"><form method="dialog" @submit=${this.confirmKillSwitch}><h2 id="kill-title">Confirm global authorization change</h2><p>Target: <code>global authorization kill switch</code></p><p>Action: <strong>${this.proposedStop ? "stop" : "resume"} all new authorizations</strong></p><label for="kill-reason">Required audit reason</label><textarea id="kill-reason" name="reason" required maxlength="1000"></textarea><menu><li><button type="button" value="cancel" @click=${this.closeKillDialog}>Cancel</button></li><li><button type="submit" value="confirm">Confirm ${this.proposedStop ? "stop" : "resume"}</button></li></menu></form></dialog>
      <dialog id="change-confirmation" part="dialog" aria-labelledby="change-title"><form method="dialog" @submit=${this.applyConfirmedChange}><h2 id="change-title">Confirm permanent change</h2><p>Target: <code>${this.pendingChange?.target ?? ""}</code></p><p>Action: <strong>${this.pendingChange?.action ?? ""}</strong></p><p>This action changes financial or pricing records and cannot be edited afterward.</p><menu><li><button type="button" value="cancel" @click=${this.cancelChange}>Cancel</button></li><li><button type="submit" value="confirm">Confirm change</button></li></menu></form></dialog>
      ${this.invitationSecret === null ? nothing : html`<dialog id="invitation-secret" part="dialog" aria-labelledby="invitation-title" @close=${this.clearInvitation}><h2 id="invitation-title">Identity invitation issued</h2><p>Copy this one-time secret now. It is held only in this page's memory and disappears when this dialog closes.</p><p><code data-secret>${this.invitationSecret}</code></p><form method="dialog"><button type="submit" @click=${this.clearInvitation}>I saved it</button></form></dialog>`}
    </och-app-shell>`
  }
}

declare global { interface HTMLElementTagNameMap { "admin-app": AdminApp } }
