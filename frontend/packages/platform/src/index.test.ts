import { Effect, Exit, Layer } from "effect"
import { FetchHttpClient } from "@effect/platform"
import { describe, expect, it } from "vitest"
import {
  ClearinghouseApi,
  ClearinghouseApiLive,
  InvalidResponse,
  RequestTransport,
  TransportUnavailable,
  makeRequestTransportFetch,
  runFrontend
} from "./index.js"

const health = Effect.gen(function* () {
  return yield* (yield* ClearinghouseApi).health
})

describe("clearinghouse API capability", () => {
  it("decodes health data through an injected transport", async () => {
    const layer = ClearinghouseApiLive.pipe(Layer.provide(
      Layer.succeed(RequestTransport, {
        requestHealth: Effect.succeed({ status: "ok", checks: { database: "ok" } })
      })
    ))
    await expect(Effect.runPromise(health.pipe(Effect.provide(layer))))
      .resolves.toEqual({ status: "ok", checks: { database: "ok" } })
  })

  it("preserves a typed transport failure", async () => {
    const failure = new TransportUnavailable({ operation: "health", cause: "offline" })
    const layer = ClearinghouseApiLive.pipe(Layer.provide(
      Layer.succeed(RequestTransport, { requestHealth: Effect.fail(failure) })
    ))
    const exit = await Effect.runPromiseExit(health.pipe(Effect.provide(layer)))
    expect(Exit.isFailure(exit)).toBe(true)
  })

  it("translates invalid boundary data", async () => {
    const layer = ClearinghouseApiLive.pipe(Layer.provide(
      Layer.succeed(RequestTransport, { requestHealth: Effect.succeed({ status: "mystery" }) })
    ))
    const result = await Effect.runPromise(health.pipe(
      Effect.provide(layer),
      Effect.flip
    ))
    expect(result).toBeInstanceOf(InvalidResponse)
  })

  it("runs browser effects through the single managed runtime", async () => {
    await expect(runFrontend(Effect.succeed("ready"))).resolves.toBe("ready")
  })

  it("provides a fetch-backed transport at the platform edge", async () => {
    const healthUrl = new URL("data:application/json,%7B%22status%22%3A%22unavailable%22%7D")
    const program = Effect.gen(function* () {
      return yield* (yield* RequestTransport).requestHealth
    }).pipe(Effect.provide(
      makeRequestTransportFetch(healthUrl).pipe(Layer.provide(FetchHttpClient.layer))
    ))
    await expect(Effect.runPromise(program)).resolves.toEqual({ status: "unavailable" })
  })
})
