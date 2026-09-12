import { Effect } from "effect"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AdminApi, ApiFailure, PayloadFailure, runAdmin } from "./api.js"

const run = <A>(use: (api: AdminApi["Type"]) => Effect.Effect<A, ApiFailure | PayloadFailure>) =>
  runAdmin(Effect.flatMap(AdminApi, use))

describe("admin API", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("decodes providers and sends mutations with CSRF protection", async () => {
    document.cookie = "och_csrf=csrf-value; path=/"
    let requestSignal: AbortSignal | null | undefined
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") {
        return Promise.resolve(new Response(JSON.stringify({ providers: ["email"] }), { status: 200 }))
      }
      expect(init?.headers).toMatchObject({ "X-CSRF-Token": "csrf-value" })
      if (path === "/v1/auth/email/code") {
        expect(init?.headers).toMatchObject({ "Content-Type": "application/json" })
      }
      return Promise.resolve(new Response(null, { status: 204 }))
    })
    vi.stubGlobal("fetch", fetchMock)
    await expect(run((api) => api.providers())).resolves.toEqual({ providers: ["email"] })
    await expect(run((api) => api.requestCode("admin@example.com"))).resolves.toBeUndefined()
    await expect(run((api) => api.logout())).resolves.toBeUndefined()
    expect(requestSignal).toBeInstanceOf(AbortSignal)
  })

  it("distinguishes HTTP, transport, and invalid-payload failures", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 403 }))))
    await expect(runAdmin(Effect.either(Effect.flatMap(AdminApi, (api) => api.session())))).resolves.toMatchObject({
      _tag: "Left", left: { _tag: "ApiFailure", status: 403 }
    })

    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))))
    await expect(runAdmin(Effect.either(Effect.flatMap(AdminApi, (api) => api.session())))).resolves.toMatchObject({
      _tag: "Left", left: { _tag: "PayloadFailure" }
    })

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("not-json", { status: 200 }))))
    await expect(runAdmin(Effect.either(Effect.flatMap(AdminApi, (api) => api.session())))).resolves.toMatchObject({
      _tag: "Left", left: { _tag: "PayloadFailure" }
    })

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 200 }))))
    await expect(runAdmin(Effect.either(Effect.flatMap(AdminApi, (api) => api.session())))).resolves.toMatchObject({
      _tag: "Left", left: { _tag: "PayloadFailure" }
    })
  })

  it("rejects malformed monetary and datetime strings before rendering", async () => {
    const at = "2026-09-11T14:00:00+00:00"
    const overview = {
      users: 1, workloads: 1, active_workloads: 1, usage: 0, unmatched_usage: 0,
      computed_fee: "20", currency: "wei",
      global_stop: { enabled: false, reason: "Normal operation", changed_at: at }
    }
    const decodeOverview = () => runAdmin(Effect.either(
      Effect.flatMap(AdminApi, (api) => api.overview())
    ))

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({ ...overview, computed_fee: "20.5" }), { status: 200 }
    ))))
    await expect(decodeOverview()).resolves.toMatchObject({
      _tag: "Left", left: { _tag: "PayloadFailure" }
    })

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({
        ...overview,
        global_stop: { ...overview.global_stop, changed_at: "September 11" }
      }), { status: 200 }
    ))))
    await expect(decodeOverview()).resolves.toMatchObject({
      _tag: "Left", left: { _tag: "PayloadFailure" }
    })
  })

  it("accepts expired workload access without treating it as a failed payload", async () => {
    const at = "2026-09-11T14:00:00+00:00"
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({
      items: [{
        id: "work_123", account_id: "acct_123", capability: "live", model: null,
        offer_id: "offer_123", quoted_price: {
          numerator: "2", denominator: "1", currency: "wei", quantity_unit: "fixed"
        }, status: "expired", client_reference: null, runner_session_id: null,
        manifest_id: null, payment_session_id: null, created_at: at, expires_at: at
      }], next_cursor: null
    }), { status: 200 }))))

    await expect(run((api) => api.workloads())).resolves.toMatchObject({
      items: [{ status: "expired" }]
    })
  })

  it("sends opaque administration cursors as query parameters", async () => {
    let requested = ""
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      requested = String(input)
      return Promise.resolve(new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200
      }))
    }))
    await run((api) => api.users("opaque+/="))
    expect(new URL(requested, location.origin).searchParams.get("cursor")).toBe("opaque+/=")
  })
})
