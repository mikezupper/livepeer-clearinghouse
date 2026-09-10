import { decodeHealth, type Health } from "@livepeer/clearinghouse-contracts"
import { HttpClient } from "@effect/platform"
import { Context, Data, Effect, Layer, ManagedRuntime } from "effect"

export class TransportUnavailable extends Data.TaggedError("TransportUnavailable")<{
  readonly operation: "health"
  readonly cause: unknown
}> {}

export class InvalidResponse extends Data.TaggedError("InvalidResponse")<{
  readonly operation: "health"
  readonly cause: unknown
}> {}

export class RequestTransport extends Context.Tag("clearinghouse/RequestTransport")<
  RequestTransport,
  { readonly requestHealth: Effect.Effect<unknown, TransportUnavailable> }
>() {}

export class ClearinghouseApi extends Context.Tag("clearinghouse/ClearinghouseApi")<
  ClearinghouseApi,
  { readonly health: Effect.Effect<Health, TransportUnavailable | InvalidResponse> }
>() {}

export const ClearinghouseApiLive = Layer.effect(
  ClearinghouseApi,
  Effect.gen(function* () {
    const transport = yield* RequestTransport
    return {
      health: transport.requestHealth.pipe(
        Effect.flatMap(decodeHealth),
        Effect.mapError((cause) => cause instanceof TransportUnavailable
          ? cause
          : new InvalidResponse({ operation: "health", cause })),
        Effect.withSpan("ClearinghouseApi.health")
      )
    }
  })
)

export const makeRequestTransportFetch = (healthUrl: URL) => Layer.effect(
  RequestTransport,
  Effect.gen(function* () {
    const client = (yield* HttpClient.HttpClient).pipe(HttpClient.filterStatusOk)
    return {
      requestHealth: client.get(healthUrl).pipe(
        Effect.flatMap((response) => response.json),
        Effect.timeout("5 seconds"),
        Effect.mapError((cause) => new TransportUnavailable({ operation: "health", cause })),
        Effect.withSpan("RequestTransport.health")
      )
    }
  })
)

const browserRuntime = ManagedRuntime.make(Layer.empty)

export const runFrontend = <A, E>(effect: Effect.Effect<A, E>): Promise<A> =>
  browserRuntime.runPromise(effect)
