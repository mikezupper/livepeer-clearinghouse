import { Context, Data, Effect, Layer, ManagedRuntime, Schema } from "effect"
import { Costs, Credentials, IssuedCredential, IssuedWorkload, Offers, Providers, Session, Summary, UsageItems, Workloads } from "./contracts.js"

export class ApiFailure extends Data.TaggedError("ApiFailure")<{
  readonly status: number; readonly operation: string
}> {}
export class InvalidPayload extends Data.TaggedError("InvalidPayload")<{
  readonly operation: string; readonly cause: unknown
}> {}
export class NetworkFailure extends Data.TaggedError("NetworkFailure")<{
  readonly operation: string; readonly cause: unknown
}> {}
export type UserFailure = ApiFailure | InvalidPayload | NetworkFailure

const csrf = (): string => document.cookie.split("; ")
  .find((value) => value.startsWith("och_csrf="))?.slice(9) ?? ""
const request = (
  operation: string, path: string, init?: RequestInit
): Effect.Effect<Response, UserFailure> => Effect.tryPromise({
  try: (signal) => fetch(path, { credentials: "same-origin", ...init, signal }),
  catch: (cause) => new NetworkFailure({ operation, cause })
}).pipe(
  Effect.timeout("10 seconds"),
  Effect.mapError((cause) => cause instanceof NetworkFailure
    ? cause : new NetworkFailure({ operation, cause })),
  Effect.flatMap((response) => response.ok
    ? Effect.succeed(response)
    : Effect.fail(new ApiFailure({ status: response.status, operation })))
)
const decode = <A, I>(
  operation: string, schema: Schema.Schema<A, I, never>, path: string, init?: RequestInit
): Effect.Effect<A, UserFailure> => request(operation, path, init).pipe(
  Effect.flatMap((response) => Effect.tryPromise({
    try: () => response.json(),
    catch: (cause) => new InvalidPayload({ operation, cause })
  })),
  Effect.flatMap(Schema.decodeUnknown(schema, { errors: "all", onExcessProperty: "error" })),
  Effect.mapError((cause) => cause instanceof ApiFailure || cause instanceof InvalidPayload || cause instanceof NetworkFailure
    ? cause : new InvalidPayload({ operation, cause }))
)
const mutation = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), "X-CSRF-Token": csrf() },
  ...(body === undefined ? {} : { body: JSON.stringify(body) })
})
const empty = (
  operation: string, path: string, init: RequestInit
): Effect.Effect<void, UserFailure> => request(operation, path, init).pipe(Effect.asVoid)
const collectionPath = (
  path: string, cursor?: string | null, filters: Readonly<Record<string, string | null>> = {}
): string => {
  const query = new URLSearchParams()
  if (cursor) query.set("cursor", cursor)
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value)
  const encoded = query.toString()
  return encoded ? `${path}?${encoded}` : path
}

export class UserApi extends Context.Tag("clearinghouse/UserApi")<UserApi, {
  readonly providers: () => Effect.Effect<typeof Providers.Type, UserFailure>
  readonly session: () => Effect.Effect<typeof Session.Type, UserFailure>
  readonly requestCode: (email: string) => Effect.Effect<void, UserFailure>
  readonly verifyCode: (email: string, code: string) => Effect.Effect<typeof Session.Type, UserFailure>
  readonly logout: () => Effect.Effect<void, UserFailure>
  readonly offers: (cursor?: string | null, capability?: string | null, model?: string | null) => Effect.Effect<typeof Offers.Type, UserFailure>
  readonly credentials: (cursor?: string | null) => Effect.Effect<typeof Credentials.Type, UserFailure>
  readonly createCredential: (name: string) => Effect.Effect<typeof IssuedCredential.Type, UserFailure>
  readonly revokeCredential: (id: string) => Effect.Effect<void, UserFailure>
  readonly workloads: (cursor?: string | null) => Effect.Effect<typeof Workloads.Type, UserFailure>
  readonly createWorkload: (offerId: string, reference: string, maxSpendWei?: string) => Effect.Effect<typeof IssuedWorkload.Type, UserFailure>
  readonly revokeWorkload: (id: string) => Effect.Effect<void, UserFailure>
  readonly usage: (cursor?: string | null) => Effect.Effect<typeof UsageItems.Type, UserFailure>
  readonly costs: (cursor?: string | null) => Effect.Effect<typeof Costs.Type, UserFailure>
  readonly summary: () => Effect.Effect<typeof Summary.Type, UserFailure>
}>() {}

export const UserApiLive = Layer.succeed(UserApi, {
  providers: () => decode("load sign-in options", Providers, "/v1/auth/providers"),
  session: () => decode("check your session", Session, "/v1/auth/session"),
  requestCode: (email) => empty("send a one-time code", "/v1/auth/email/code", mutation("POST", { email })),
  verifyCode: (email, code) => decode("verify your sign-in code", Session, "/v1/auth/email/verify", mutation("POST", { email, code })),
  logout: () => empty("sign out", "/v1/auth/session", mutation("DELETE")),
  offers: (cursor, capability, model) => decode("load network offers", Offers, collectionPath("/v1/offers", cursor, { capability: capability ?? null, model: model ?? null })),
  credentials: (cursor) => decode("load account API credentials", Credentials, collectionPath("/v1/credentials", cursor)),
  createCredential: (name) => decode("create an account API credential", IssuedCredential, "/v1/credentials", mutation("POST", { name })),
  revokeCredential: (id) => empty("revoke the account API credential", `/v1/credentials/${encodeURIComponent(id)}`, mutation("DELETE")),
  workloads: (cursor) => decode("load workloads", Workloads, collectionPath("/v1/workloads", cursor)),
  createWorkload: (offer_id, client_reference, max_spend_wei) => decode("create workload access", IssuedWorkload, "/v1/workloads", mutation("POST", { offer_id, client_reference, ...(max_spend_wei === undefined ? {} : { max_spend_wei }) })),
  revokeWorkload: (id) => empty("revoke workload access", `/v1/workloads/${encodeURIComponent(id)}`, mutation("DELETE")),
  usage: (cursor) => decode("load usage events", UsageItems, collectionPath("/v1/usage", cursor)),
  costs: (cursor) => decode("load usage and cost", Costs, collectionPath("/v1/costs", cursor)),
  summary: () => decode("load your account summary", Summary, "/v1/summary")
})
const runtime = ManagedRuntime.make(UserApiLive)
export const runUser = <A>(effect: Effect.Effect<A, UserFailure, UserApi>): Promise<A> => runtime.runPromise(effect)
export const forkUser = <A>(
  effect: Effect.Effect<A, UserFailure, UserApi>,
  onSuccess: (value: A) => void,
  onFailure: (failure: UserFailure) => void
): void => {
  runtime.runFork(effect.pipe(Effect.matchEffect({
    onFailure: (failure) => Effect.sync(() => onFailure(failure)),
    onSuccess: (value) => Effect.sync(() => onSuccess(value))
  })))
}
