import { Effect } from "effect"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiFailure, InvalidPayload, NetworkFailure, runUser, UserApi, type UserFailure } from "./api.js"

const ids = {
  account: "account_abcdefgh",
  principal: "principal_abcdefgh",
  tenant: "tenant_abcdefgh",
  credential: "credential_abcdefgh",
  session: "session_abcdefgh",
  lease: "lease_abcdefgh",
  link: "link_abcdefgh",
  event: "event_abcdefgh",
  reservation: "reservation_abcdefgh",
  rate: "rate_abcdefgh",
  producer: "producer_abcdefgh",
  charge: "charge_abcdefgh"
} as const
const instant = "2026-09-09T12:00:00Z"
const auth = { principal_id: ids.principal, tenant_id: ids.tenant, account_id: ids.account, roles: ["credential_holder"], expires_at: instant }
const account = { id: ids.account, tenant_id: ids.tenant, display_name: "Studio", unit: "wei", exposure_cap: "1000", status: "active", created_at: instant }
const balance = { account_id: ids.account, posted: { amount: "900", unit: "wei" }, open_lease_exposure: { amount: "100", unit: "wei" }, available: { amount: "800", unit: "wei" } }
const credential = { id: ids.credential, account_id: ids.account, principal_id: ids.principal, prefix: "och_1234", label: "Build", status: "active", created_at: instant }
const lease = { id: ids.lease, cap: "100", available: "90", pending: "5", settled: "5", unit: "wei", expires_at: instant }
const signer = { id: ids.session, token: "x".repeat(40), signer_url: "https://signer.test", discovery_url: "https://signer.test/discovery", expires_at: instant, lease }
const catalog = { capability: "video.generate", model: null, rate: { numerator: "2", denominator: "1", charge_unit: "wei", quantity_unit: "fixed" }, available: true }
const usage = {
  schema_version: "1.0", event_id: ids.event, reservation_id: ids.reservation, lease_id: ids.lease,
  tenant_id: ids.tenant, account_id: ids.account, principal_id: ids.principal, capability: "video.generate",
  quantity: { value: "1", unit: "fixed" },
  price_snapshot: { rate_numerator: "2", rate_denominator: "1", charge_unit: "wei", quantity_unit: "fixed", source: "signer", source_version: "1" },
  producer: { id: ids.producer, kind: "signer", software: "go-livepeer", software_version: "1" },
  occurred_at: instant, source: { kind: "go_livepeer_create_signed_ticket", event_id: "wire-1", confirmation: "kafka", signed_current_time: instant, signed_current_time_unix_ns: "1" }
}
const charge = {
  id: ids.charge, usage_event_id: ids.event, reservation_id: ids.reservation, lease_id: ids.lease,
  tenant_id: ids.tenant, account_id: ids.account, amount: { value: "2", unit: "wei" },
  price_snapshot: { rate_card_id: ids.rate, rate_numerator: "2", rate_denominator: "1", quantity_unit: "fixed" }, created_at: instant
}
const response = (body: unknown, status = 200): Response => new Response(body === undefined ? null : JSON.stringify(body), {
  status, headers: body === undefined ? {} : { "Content-Type": "application/json" }
})
const apiEffect = <A>(use: (api: UserApi["Type"]) => Effect.Effect<A, UserFailure>) => Effect.flatMap(UserApi, use)

describe("UserApi", () => {
  beforeEach(() => { document.cookie = "och_csrf=csrf-value; Path=/" })
  afterEach(() => {
    vi.unstubAllGlobals()
    document.cookie = "och_csrf=; Max-Age=0; Path=/"
  })

  it("executes every canonical user operation with cookies, CSRF, and exact decoding", async () => {
    const requests: Request[] = []
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(new URL(String(input), location.origin), init)
      requests.push(request)
      const path = new URL(request.url).pathname
      if (path === "/v1/auth/providers") return Promise.resolve(response({ providers: ["email", "google", "github"] }))
      if (path === "/v1/auth/session" || path.endsWith("/refresh") && path.startsWith("/v1/auth/session")) return Promise.resolve(response(auth))
      if (path === "/v1/auth/email/code" || request.method === "DELETE") return Promise.resolve(response(undefined, request.method === "DELETE" ? 204 : 202))
      if (path === "/v1/auth/email/verify") return Promise.resolve(response(auth))
      if (path === "/v1/auth/identity-links") return Promise.resolve(response({ id: ids.link, provider: "email", principal_id: ids.principal, tenant_id: ids.tenant, linked_at: instant }))
      if (path === `/v1/accounts/${ids.account}`) return Promise.resolve(response(account))
      if (path === `/v1/balances/${ids.account}`) return Promise.resolve(response(balance))
      if (path === "/v1/credentials" && request.method === "GET") return Promise.resolve(response([credential]))
      if (path.startsWith("/v1/credentials")) return Promise.resolve(response({ credential, secret: "c".repeat(40) }))
      if (path === "/v1/catalog") return Promise.resolve(response([catalog]))
      if (path === "/v1/sessions" && request.method === "GET") return Promise.resolve(response({ items: [{ ...signer, token: undefined }] }, 200))
      if (path.startsWith("/v1/sessions")) return Promise.resolve(response(signer, 201))
      if (path === "/v1/usage") return Promise.resolve(response({ items: [usage], page: { next_cursor: null } }))
      return Promise.resolve(response({ items: [charge], page: { next_cursor: null } }))
    }))

    const results = await Promise.all([
      runUser(apiEffect((api) => api.providers())),
      runUser(apiEffect((api) => api.session())),
      runUser(apiEffect((api) => api.requestCode("user@example.org"))),
      runUser(apiEffect((api) => api.verifyCode("user@example.org", "123456"))),
      runUser(apiEffect((api) => api.redeemInvitation("och_inv_" + "x".repeat(50)))),
      runUser(apiEffect((api) => api.logout())),
      runUser(apiEffect((api) => api.refreshBrowser())),
      runUser(apiEffect((api) => api.account(ids.account))),
      runUser(apiEffect((api) => api.balance(ids.account))),
      runUser(apiEffect((api) => api.credentials())),
      runUser(apiEffect((api) => api.issueCredential(ids.account, ids.principal, "Build"))),
      runUser(apiEffect((api) => api.rotateCredential(ids.credential))),
      runUser(apiEffect((api) => api.revokeCredential(ids.credential))),
      runUser(apiEffect((api) => api.catalog())),
      runUser(apiEffect((api) => api.signerSessions())),
      runUser(apiEffect((api) => api.createSignerSession({ capability: "video.generate", app: "demo", requested_cap: "100", unit: "wei", ttl_seconds: 3600 }, "create-command-0001"))),
      runUser(apiEffect((api) => api.refreshSignerSession(ids.session, "refresh-command-0001"))),
      runUser(apiEffect((api) => api.revokeSignerSession(ids.session))),
      runUser(apiEffect((api) => api.usage(ids.account))),
      runUser(apiEffect((api) => api.charges(ids.account)))
    ])
    expect(results[0].providers).toEqual(["email", "google", "github"])
    expect(results[7]).toMatchObject({ display_name: "Studio" })
    expect(requests.every((request) => request.credentials === "same-origin")).toBe(true)
    const mutationRequests = requests.filter((request) => !["GET", "HEAD"].includes(request.method))
    expect(mutationRequests.some((request) => request.headers.get("X-CSRF-Token") === "csrf-value")).toBe(true)
    expect(requests.some((request) => request.url.includes(`account_id=${ids.account}`))).toBe(true)
    const create = requests.find((request) => request.method === "POST" && new URL(request.url).pathname === "/v1/sessions")
    const refresh = requests.find((request) => new URL(request.url).pathname === `/v1/sessions/${ids.session}/refresh`)
    expect(create?.headers.get("Idempotency-Key")).toBe("create-command-0001")
    expect(refresh?.headers.get("Idempotency-Key")).toBe("refresh-command-0001")
    const usageRequest = requests.find((request) => new URL(request.url).pathname === "/v1/usage")
    const chargeRequest = requests.find((request) => new URL(request.url).pathname === "/v1/charges")
    expect(new URL(usageRequest?.url ?? location.href).search).toBe(`?account_id=${ids.account}&limit=50`)
    expect(new URL(chargeRequest?.url ?? location.href).search).toBe(`?account_id=${ids.account}&limit=50`)
    expect(requests.map((request) => new URL(request.url).pathname)).toContain(
      `/v1/balances/${ids.account}`
    )
    expect(requests.some((request) => new URL(request.url).pathname.endsWith("/balance"))).toBe(false)
  })

  it("forwards bounded cursors without changing caller-owned idempotency keys", async () => {
    const requests: Request[] = []
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(new URL(String(input), location.origin), init)
      requests.push(request)
      return Promise.resolve(response(new URL(request.url).pathname === "/v1/usage"
        ? { items: [usage], page: { next_cursor: null } }
        : { items: [charge], page: { next_cursor: null } }))
    }))

    await runUser(apiEffect((api) => api.usage(ids.account, "usage_cursor_2")))
    await runUser(apiEffect((api) => api.charges(ids.account, "charge_cursor_2")))

    expect(new URL(requests[0]?.url ?? location.href).search).toBe(
      `?account_id=${ids.account}&limit=50&cursor=usage_cursor_2`
    )
    expect(new URL(requests[1]?.url ?? location.href).search).toBe(
      `?account_id=${ids.account}&limit=50&cursor=charge_cursor_2`
    )
  })

  it("returns typed failures for HTTP, invalid payload, and transport failures", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response({}, 403))))
    const http = await runUser(apiEffect((api) => api.providers()).pipe(Effect.either))
    expect(http._tag === "Left" && http.left instanceof ApiFailure).toBe(true)
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response({ providers: ["unknown"] }))))
    const invalid = await runUser(apiEffect((api) => api.providers()).pipe(Effect.either))
    expect(invalid._tag === "Left" && invalid.left instanceof InvalidPayload).toBe(true)
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))))
    const network = await runUser(apiEffect((api) => api.providers()).pipe(Effect.either))
    expect(network._tag === "Left" && network.left instanceof NetworkFailure).toBe(true)
  })

  it("omits CSRF and content type for bodyless reads and deletes when cookie is absent", async () => {
    document.cookie = "och_csrf=; Max-Age=0; Path=/"
    const calls: Request[] = []
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push(new Request(new URL(String(input), location.origin), init))
      return Promise.resolve(response(undefined, 204))
    }))
    await runUser(apiEffect((api) => api.logout()))
    expect(calls[0]?.headers.has("X-CSRF-Token")).toBe(false)
    expect(calls[0]?.headers.has("Content-Type")).toBe(false)
  })
})
