import type { Page, Request, Route } from "@playwright/test"

export const appUrl = { admin: "http://127.0.0.1:4173", user: "http://127.0.0.1:4174" } as const
const at = "2026-09-09T12:00:00Z"
const ids = {
  tenant: "tenant_abcdefgh", account: "account_abcdefgh", principal: "principal_abcdefgh",
  grant: "grant_abcdefgh", lease: "lease_abcdefgh", usage: "usage_abcdefgh",
  reservation: "receipt_abcdefgh", charge: "charge_abcdefgh", rate: "rate_abcdefgh",
  producer: "signer_abcdefgh", credential: "credential_abcdefgh", session: "session_abcdefgh"
} as const
const session = { principal_id: ids.principal, tenant_id: ids.tenant, account_id: ids.account, roles: ["operator"], expires_at: at }
const tenant = { id: ids.tenant, display_name: "Video team", status: "active", created_at: at }
const account = { id: ids.account, tenant_id: ids.tenant, display_name: "Production studio", unit: "wei", exposure_cap: "1000", status: "active", created_at: at }
const balance = { account_id: ids.account, posted: { amount: "900", unit: "wei" }, open_lease_exposure: { amount: "100", unit: "wei" }, available: { amount: "800", unit: "wei" } }
const grant = { id: ids.grant, account_id: ids.account, kind: "credit", amount: { amount: "900", unit: "wei" }, reason: "Initial allocation", external_reference: null, actor_id: ids.principal, created_at: at }
const usage = {
  schema_version: "1.0", event_id: ids.usage, reservation_id: ids.reservation, lease_id: ids.lease,
  tenant_id: ids.tenant, account_id: ids.account, principal_id: ids.principal, capability: "video.generate",
  quantity: { value: "1", unit: "fixed" },
  price_snapshot: { rate_numerator: "2", rate_denominator: "1", charge_unit: "wei", quantity_unit: "fixed", source: "signer", source_version: "1" },
  producer: { id: ids.producer, kind: "signer", software: "go-livepeer", software_version: "1" }, occurred_at: at,
  source: { kind: "go_livepeer_create_signed_ticket", event_id: "wire-event", confirmation: "kafka", signed_current_time: at, signed_current_time_unix_ns: "1788955200000000000" }
}
const charge = { id: ids.charge, usage_event_id: ids.usage, reservation_id: ids.reservation, lease_id: ids.lease, tenant_id: ids.tenant, account_id: ids.account, amount: { value: "2", unit: "wei" }, price_snapshot: { rate_card_id: ids.rate, rate_numerator: "2", rate_denominator: "1", quantity_unit: "fixed" }, created_at: at }

const page = (items: readonly unknown[]) => ({ items, page: { next_cursor: null } })
const reply = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  headers: { "Cache-Control": "no-store", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
  body: body === undefined ? "" : JSON.stringify(body)
})

export interface MockApi {
  readonly requests: Request[]
}

export const installMockApi = (browserPage: Page, application: "admin" | "user", initiallySignedIn = true): Promise<MockApi> => {
  let signedIn = initiallySignedIn
  const requests: Request[] = []
  return browserPage.route("**/v1/**", (route) => {
    const request = route.request()
    requests.push(request)
    const { pathname } = new URL(request.url())
    if (pathname === "/v1/auth/providers") return reply(route, { providers: ["email", "google", "github"] })
    if (pathname === "/v1/auth/session" && request.method() === "GET") return signedIn ? reply(route, session) : reply(route, {}, 401)
    if (pathname === "/v1/auth/email/code") return reply(route, undefined, 202)
    if (pathname === "/v1/auth/email/verify") { signedIn = true; return reply(route, session) }
    if (pathname === "/v1/auth/session/refresh") return reply(route, session)
    if (pathname === "/v1/auth/session" && request.method() === "DELETE") { signedIn = false; return reply(route, undefined, 204) }
    if (request.method() !== "GET") return reply(route, undefined, 204)

    if (application === "user") {
      if (pathname === `/v1/accounts/${ids.account}`) return reply(route, account)
      if (pathname === `/v1/balances/${ids.account}`) return reply(route, balance)
      if (pathname === "/v1/usage") return reply(route, page([usage]))
      if (pathname === "/v1/charges") return reply(route, page([charge]))
      if (pathname === "/v1/catalog") return reply(route, [])
      if (pathname === "/v1/credentials") return reply(route, [])
      if (pathname === "/v1/sessions") return reply(route, { items: [] })
    }

    const adminResponses: Readonly<Record<string, unknown>> = {
      "/v1/tenants": page([tenant]), "/v1/accounts": page([account]), "/v1/principals": [],
      "/v1/grants": page([grant]), "/v1/leases": { items: [] }, "/v1/usage": page([usage]),
      "/v1/charges": page([charge]), "/v1/open-reservations": page([]), "/v1/rate-cards": [],
      "/v1/operations/reconciliation": page([]), "/v1/operations/audit-events": page([]),
      "/v1/operations/metering-health": { status: "ready", open_cases: 0, quarantined: 0, unresolved: 0, global_exposure_cap: "1000", global_open_exposure: "100", last_checkpoint_at: at, last_heartbeat_at: at },
      "/v1/operations/adapters": [],
      "/v1/operations/kill-switch": { enabled: false, reason: "Normal operation", changed_at: at, actor_id: null },
      [`/v1/balances/${ids.account}`]: balance
    }
    return pathname in adminResponses ? reply(route, adminResponses[pathname]) : reply(route, {}, 404)
  }).then(() => ({ requests }))
}

export { ids }
