import type { Page, Request, Route } from "@playwright/test"

export const appUrl = { admin: "http://127.0.0.1:4173", user: "http://127.0.0.1:4174" } as const
const at = "2026-09-11T12:00:00Z"
export const ids = {
  user: "usr_abcdefgh", account: "acct_abcdefgh", credential: "cred_abcdefgh",
  offer: "price_abcdefgh", workload: "work_abcdefgh", usage: "usage_abcdefgh"
} as const
const session = { user_id: ids.user, account_id: ids.account, email: "member@example.test", is_admin: false, expires_at: at }
const adminSession = { ...session, user_id: "usr_admin", account_id: "acct_admin", email: "admin@example.test", is_admin: true }
const price = { numerator: "2", denominator: "1", currency: "wei", quantity_unit: "pixel" }
const offer = {
  id: ids.offer, runner_url: "https://runner.example.test/live",
  orchestrator_address: "0x0000000000000000000000000000000000000001",
  capability: "live-video-to-video", model: "noop", constraints: { gpu: "L40S" },
  price, observed_at: at, expires_at: at
}
const workload = {
  id: ids.workload, account_id: ids.account, capability: offer.capability, model: offer.model,
  offer_id: ids.offer, quoted_price: price, status: "active", client_reference: "sdk-job-1",
  runner_session_id: null, manifest_id: "manifest-1", payment_session_id: "pm-1",
  max_spend_wei: "100",
  created_at: at, expires_at: at
}
const usage = {
  id: ids.usage, workload_id: ids.workload, manifest_id: "manifest-1", payment_session_id: "pm-1",
  capability: offer.capability, quantity: "10", quantity_unit: "pixel", computed_fee: "20",
  currency: "wei", ticket_count: 1, sequence_number: 0, occurred_at: at, status: "matched"
}
const cost = {
  workload, measured_quantity: "10", measured_unit: "pixel", quoted_fee: "20",
  computed_fee: "20", currency: "wei", event_count: 1, spend_ceiling: "100",
  authorized_fee: "30", pending_fee: "10", remaining_spend: "70"
}

const reply = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  headers: { "Cache-Control": "no-store", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
  body: body === undefined ? "" : JSON.stringify(body)
})

export interface MockApi { readonly requests: Request[] }

export const installMockApi = async (
  browserPage: Page, application: "admin" | "user", initiallySignedIn = true
): Promise<MockApi> => {
  let signedIn = initiallySignedIn
  let stopped = false
  const requests: Request[] = []
  await browserPage.route("**/v1/**", (route) => {
    const request = route.request()
    requests.push(request)
    const { pathname } = new URL(request.url())
    if (pathname === "/v1/auth/providers") return reply(route, { providers: ["email", "google", "github"] })
    if (pathname === "/v1/auth/session" && request.method() === "GET") {
      return signedIn ? reply(route, application === "admin" ? adminSession : session) : reply(route, {}, 401)
    }
    if (pathname === "/v1/auth/email/code") return reply(route, undefined, 202)
    if (pathname === "/v1/auth/email/verify") {
      signedIn = true
      return reply(route, application === "admin" ? adminSession : session)
    }
    if (pathname === "/v1/auth/session" && request.method() === "DELETE") {
      signedIn = false
      return reply(route, undefined, 204)
    }
    if (pathname === "/v1/offers") return reply(route, { items: [offer], next_cursor: null })
    if (pathname === "/v1/workloads" && request.method() === "GET") return reply(route, { items: [workload], next_cursor: null })
    if (pathname === "/v1/workloads" && request.method() === "POST") {
      return reply(route, { ...workload, token: "och_work_secret", sdk_token: "sdk-token", signer_url: appUrl.user, discovery_url: `${appUrl.user}/v1/discovery` }, 201)
    }
    if (pathname.startsWith("/v1/workloads/") && request.method() === "DELETE") return reply(route, undefined, 204)
    if (pathname === "/v1/credentials" && request.method() === "GET") {
      return reply(route, { items: [{ id: ids.credential, name: "Gateway automation", created_at: at, revoked_at: null }], next_cursor: null })
    }
    if (pathname === "/v1/credentials" && request.method() === "POST") {
      return reply(route, { id: ids.credential, name: "Gateway automation", token: "och_live_secret", created_at: at }, 201)
    }
    if (pathname.startsWith("/v1/credentials/") && request.method() === "DELETE") return reply(route, undefined, 204)
    if (pathname === "/v1/usage") return reply(route, { items: [usage], next_cursor: null })
    if (pathname === "/v1/costs") return reply(route, { items: [cost], next_cursor: null })
    if (pathname === "/v1/summary") return reply(route, { offers: 1, credentials: 1, workloads: 1, active_workloads: 1, usage_events: 1, computed_fee: "20", currency: "wei" })
    if (pathname === "/v1/admin/global-stop" && request.method() === "PUT") {
      stopped = true
      return reply(route, { enabled: true, reason: "maintenance", changed_at: at })
    }
    if (pathname === "/v1/admin/overview") return reply(route, {
      users: 2, workloads: 1, active_workloads: 1, usage: 1, unmatched_usage: 0,
      computed_fee: "20", currency: "wei",
      global_stop: { enabled: stopped, reason: stopped ? "maintenance" : "Normal operation", changed_at: at }
    })
    if (pathname === "/v1/admin/users") return reply(route, { items: [adminSession, session], next_cursor: null })
    if (pathname === "/v1/admin/workloads") return reply(route, { items: [workload], next_cursor: null })
    return reply(route, {}, 404)
  })
  return { requests }
}
