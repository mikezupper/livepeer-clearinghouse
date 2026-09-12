import { Context, Data, Effect, Layer, ManagedRuntime, Schema } from "effect"
import { Overview, Providers, Session, Stop, Users, Workloads } from "./domain.js"

export class ApiFailure extends Data.TaggedError("ApiFailure")<{ readonly status: number }> {}
export class PayloadFailure extends Data.TaggedError("PayloadFailure")<{ readonly cause: unknown }> {}
export type Failure = ApiFailure | PayloadFailure
const csrf = (): string => document.cookie.split("; ").find((value) => value.startsWith("och_csrf="))?.slice(9) ?? ""
const response = (path: string, init?: RequestInit): Effect.Effect<Response, Failure> => Effect.tryPromise({
  try: (signal) => fetch(path, { credentials: "same-origin", ...init, signal }),
  catch: (cause) => new PayloadFailure({ cause })
}).pipe(
  Effect.timeout("10 seconds"),
  Effect.mapError((cause) => cause instanceof PayloadFailure ? cause : new PayloadFailure({ cause })),
  Effect.flatMap((value) => value.ok ? Effect.succeed(value) : Effect.fail(new ApiFailure({ status: value.status })))
)
const decode = <A, I>(schema: Schema.Schema<A, I, never>, path: string, init?: RequestInit): Effect.Effect<A, Failure> => response(path, init).pipe(
  Effect.flatMap((value) => Effect.tryPromise({ try: () => value.json(), catch: (cause) => new PayloadFailure({ cause }) })),
  Effect.flatMap(Schema.decodeUnknown(schema)),
  Effect.mapError((cause) => cause instanceof ApiFailure || cause instanceof PayloadFailure ? cause : new PayloadFailure({ cause }))
)
const mutation = (method: string, body?: unknown): RequestInit => ({
  method, headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), "X-CSRF-Token": csrf() },
  ...(body === undefined ? {} : { body: JSON.stringify(body) })
})
const collectionPath = (path: string, cursor?: string | null): string => cursor
  ? `${path}?${new URLSearchParams({ cursor }).toString()}` : path
export class AdminApi extends Context.Tag("clearinghouse/AdminApi")<AdminApi, {
  readonly providers: () => Effect.Effect<typeof Providers.Type, Failure>
  readonly session: () => Effect.Effect<typeof Session.Type, Failure>
  readonly requestCode: (email: string) => Effect.Effect<void, Failure>
  readonly verifyCode: (email: string, code: string) => Effect.Effect<typeof Session.Type, Failure>
  readonly logout: () => Effect.Effect<void, Failure>
  readonly overview: () => Effect.Effect<typeof Overview.Type, Failure>
  readonly users: (cursor?: string | null) => Effect.Effect<typeof Users.Type, Failure>
  readonly workloads: (cursor?: string | null) => Effect.Effect<typeof Workloads.Type, Failure>
  readonly setStop: (enabled: boolean, reason: string) => Effect.Effect<typeof Stop.Type, Failure>
}>() {}
export const AdminApiLive = Layer.succeed(AdminApi, {
  providers: () => decode(Providers, "/v1/auth/providers"),
  session: () => decode(Session, "/v1/auth/session"),
  requestCode: (email) => response("/v1/auth/email/code", mutation("POST", { email })).pipe(Effect.asVoid),
  verifyCode: (email, code) => decode(Session, "/v1/auth/email/verify", mutation("POST", { email, code })),
  logout: () => response("/v1/auth/session", mutation("DELETE")).pipe(Effect.asVoid),
  overview: () => decode(Overview, "/v1/admin/overview"),
  users: (cursor) => decode(Users, collectionPath("/v1/admin/users", cursor)),
  workloads: (cursor) => decode(Workloads, collectionPath("/v1/admin/workloads", cursor)),
  setStop: (enabled, reason) => decode(Stop, "/v1/admin/global-stop", mutation("PUT", { enabled, reason }))
})
const runtime = ManagedRuntime.make(AdminApiLive)
export const runAdmin = <A>(effect: Effect.Effect<A, Failure, AdminApi>): Promise<A> => runtime.runPromise(effect)
export const forkAdmin = <A>(
  effect: Effect.Effect<A, Failure, AdminApi>,
  onSuccess: (value: A) => void,
  onFailure: (failure: Failure) => void
): void => {
  runtime.runFork(effect.pipe(Effect.matchEffect({
    onFailure: (failure) => Effect.sync(() => onFailure(failure)),
    onSuccess: (value) => Effect.sync(() => onSuccess(value))
  })))
}
