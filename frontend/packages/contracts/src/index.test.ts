import { Effect, Either, Schema } from "effect"
import { describe, expect, it } from "vitest"
import { Account, Health, decodeAccount, decodeHealth, isHealthAvailable } from "./index.js"

const validAccount = {
  id: "acct_12345678",
  tenant_id: "tenant_abcdefgh",
  display_name: "Primary account",
  unit: "wei",
  exposure_cap: "1000000",
  status: "active",
  created_at: "2026-09-09T20:00:00.000Z"
} as const

describe("canonical OpenAPI boundary contracts", () => {
  it("round-trips the Account wire schema without changing snake_case", async () => {
    const account = await Effect.runPromise(decodeAccount(validAccount))
    const encoded = Schema.encodeSync(Account)(account)
    expect(encoded).toEqual(validAccount)
  })

  it("rejects the removed AccountSummary camelCase and role shape", () => {
    const result = Schema.decodeUnknownEither(Account, { onExcessProperty: "error" })({
      ...validAccount,
      displayName: "Primary account",
      role: "admin"
    })
    expect(Either.isLeft(result)).toBe(true)
  })

  it.each([
    { ...validAccount, id: "acct_short" },
    { ...validAccount, exposure_cap: "-1" },
    { ...validAccount, status: "admin" },
    { ...validAccount, created_at: "yesterday" }
  ])("rejects an Account value outside the OpenAPI constraints", (payload) => {
    const result = Schema.decodeUnknownEither(Account, { onExcessProperty: "error" })(payload)
    expect(Either.isLeft(result)).toBe(true)
  })

  it("accepts both Health states and optional dependency checks", async () => {
    const ok = await Effect.runPromise(decodeHealth({
      status: "ok",
      checks: { database: "ok", kafka: "disabled" }
    }))
    const unavailable = await Effect.runPromise(decodeHealth({ status: "unavailable" }))
    expect(Schema.encodeSync(Health)(ok)).toEqual({
      status: "ok",
      checks: { database: "ok", kafka: "disabled" }
    })
    expect(Schema.encodeSync(Health)(unavailable)).toEqual({ status: "unavailable" })
    expect(isHealthAvailable(ok)).toBe(true)
    expect(isHealthAvailable(await Effect.runPromise(decodeHealth({ status: "ok" })))).toBe(true)
    expect(isHealthAvailable(unavailable)).toBe(false)
    expect(isHealthAvailable(await Effect.runPromise(decodeHealth({
      status: "ok", checks: { database: "unavailable" }
    })))).toBe(false)
  })

  it.each([
    { status: "degraded" },
    { status: "ok", service: "clearinghouse" },
    { status: "ok", checks: { database: "degraded" } }
  ])("rejects a non-contract Health payload: $status", (payload) => {
    const result = Schema.decodeUnknownEither(Health, { onExcessProperty: "error" })(payload)
    expect(Either.isLeft(result)).toBe(true)
  })
})
