import { Effect, Either, Layer, ManagedRuntime } from "effect"
import { AdminApi, type AdminApiShape } from "./api.js"

const runtime = ManagedRuntime.make(Layer.empty)

export const runAdmin = <A, E>(effect: Effect.Effect<A, E>): Promise<Either.Either<A, E>> =>
  runtime.runPromise(Effect.either(effect))

export const provideAdminApi = <A, E>(api: AdminApiShape, effect: Effect.Effect<A, E, AdminApi>) =>
  effect.pipe(Effect.provideService(AdminApi, api))
