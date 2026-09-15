import { Effect } from "effect"
import { afterEach, describe, expect, it, vi } from "vitest"
import { InvalidPayload, runUser, UserApi, type UserFailure } from "./api.js"

const at = "2026-09-11T14:00:00+00:00"
const price = { numerator: "2", denominator: "1", currency: "wei", quantity_unit: "pixel" }
const workload = {
  id: "work_123", account_id: "acct_123", capability: "live", model: null,
  offer_id: "offer_123", quoted_price: price, status: "active",
  client_reference: null, runner_session_id: null, manifest_id: null,
  payment_session_id: null, max_spend_wei: null, created_at: at, expires_at: at
}
const cost = {
  workload, measured_quantity: "10", measured_unit: "pixel", quoted_fee: "20",
  computed_fee: "20", currency: "wei", event_count: 1, spend_ceiling: null,
  authorized_fee: "20", pending_fee: "0", remaining_spend: null
}
const run = <A>(use: (api: UserApi["Type"]) => Effect.Effect<A, UserFailure>) =>
  runUser(Effect.flatMap(UserApi, use))
const json = (body: unknown): Response => new Response(JSON.stringify(body), { status: 200 })

describe("user API boundaries", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("rejects malformed monetary strings before they reach BigInt rendering", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({
      items: [{ ...cost, computed_fee: "not-an-integer" }], next_cursor: null
    }))))

    const result = await runUser(Effect.either(Effect.flatMap(UserApi, (api) => api.costs())))
    expect(result._tag === "Left" && result.left instanceof InvalidPayload).toBe(true)
  })

  it("rejects malformed datetime strings at the response boundary", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({
      items: [{ id: "cred_123", name: "Python", created_at: "yesterday", revoked_at: null }], next_cursor: null
    }))))

    const result = await runUser(Effect.either(Effect.flatMap(UserApi, (api) => api.credentials())))
    expect(result._tag === "Left" && result.left instanceof InvalidPayload).toBe(true)
  })

  it("passes a finite-timeout cancellation signal to fetch", async () => {
    let requestSignal: AbortSignal | null | undefined
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal
      return Promise.resolve(json({ providers: ["email"] }))
    }))

    await expect(run((api) => api.providers())).resolves.toEqual({ providers: ["email"] })
    expect(requestSignal).toBeInstanceOf(AbortSignal)
  })

  it("accepts an expired workload as a distinct access state", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({
      items: [{ ...workload, status: "expired" }], next_cursor: null
    }))))

    await expect(run((api) => api.workloads())).resolves.toMatchObject({
      items: [{ id: "work_123", status: "expired" }]
    })
  })

  it("accepts signer usage carrying its non-negative sequence number", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({
      items: [{
        id: "usage_123", workload_id: "work_123", manifest_id: "manifest_123",
        payment_session_id: "payment_123", capability: "live", quantity: "10",
        quantity_unit: "nanosecond", computed_fee: "20", currency: "wei",
        ticket_count: 1, sequence_number: 2, occurred_at: at, status: "matched"
      }], next_cursor: null
    }))))

    await expect(run((api) => api.usage())).resolves.toMatchObject({
      items: [{ id: "usage_123", sequence_number: 2 }]
    })
  })

  it("encodes opaque cursors and offer filters without exposing them to schemas", async () => {
    let requested = ""
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      requested = String(input)
      return Promise.resolve(json({ items: [], next_cursor: null }))
    }))
    await run((api) => api.offers("opaque+/=", "live video", "model/a"))
    const url = new URL(requested, location.origin)
    expect(url.searchParams.get("cursor")).toBe("opaque+/=")
    expect(url.searchParams.get("capability")).toBe("live video")
    expect(url.searchParams.get("model")).toBe("model/a")
  })
})
