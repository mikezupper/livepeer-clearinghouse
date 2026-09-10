import { Context, Data, Effect, Layer, ManagedRuntime, Schema } from "effect"
import {
  Account,
  AuthSession,
  Balance,
  Catalog,
  ChargePage,
  Credentials,
  IdentityLink,
  IssuedCredential,
  type NewSignerSession,
  Providers,
  SignerSession,
  SignerSessions,
  UsagePage
} from "./contracts.js"

export class ApiFailure extends Data.TaggedError("ApiFailure")<{
  readonly status: number
  readonly operation: string
}> {}
export class InvalidPayload extends Data.TaggedError("InvalidPayload")<{
  readonly operation: string
  readonly cause: unknown
}> {}
export class NetworkFailure extends Data.TaggedError("NetworkFailure")<{
  readonly operation: string
  readonly cause: unknown
}> {}
export type UserFailure = ApiFailure | InvalidPayload | NetworkFailure

const csrfToken = (): string | undefined => document.cookie
  .split(";")
  .map((part) => part.trim())
  .find((part) => part.startsWith("och_csrf="))
  ?.slice("och_csrf=".length)

const json = <A, I>(operation: string, schema: Schema.Schema<A, I, never>, path: string, init?: RequestInit) =>
  Effect.tryPromise({
    try: () => fetch(path, { credentials: "same-origin", ...init }),
    catch: (cause) => new NetworkFailure({ operation, cause })
  }).pipe(
    Effect.flatMap((response) => Effect.if(response.ok, {
      onTrue: () => Effect.tryPromise({
        try: () => response.json(),
        catch: (cause) => new InvalidPayload({ operation, cause })
      }),
      onFalse: () => Effect.fail(new ApiFailure({ status: response.status, operation }))
    })),
    Effect.flatMap(Schema.decodeUnknown(schema, { errors: "all", onExcessProperty: "error" })),
    Effect.mapError((cause) => cause instanceof ApiFailure || cause instanceof InvalidPayload || cause instanceof NetworkFailure
      ? cause
      : new InvalidPayload({ operation, cause }))
  )

const empty = (operation: string, path: string, init: RequestInit) =>
  Effect.tryPromise({
    try: () => fetch(path, { credentials: "same-origin", ...init }),
    catch: (cause) => new NetworkFailure({ operation, cause })
  }).pipe(Effect.flatMap((response) => response.ok
    ? Effect.void
    : Effect.fail(new ApiFailure({ status: response.status, operation }))))

const mutation = (method: string, body?: unknown): RequestInit => {
  const csrf = csrfToken()
  return {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(csrf === undefined ? {} : { "X-CSRF-Token": csrf })
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  }
}

export class UserApi extends Context.Tag("user-web/UserApi")<UserApi, {
  readonly providers: () => Effect.Effect<typeof Providers.Type, UserFailure>
  readonly session: () => Effect.Effect<typeof AuthSession.Type, UserFailure>
  readonly requestCode: (email: string) => Effect.Effect<void, UserFailure>
  readonly verifyCode: (email: string, code: string) => Effect.Effect<typeof AuthSession.Type, UserFailure>
  readonly redeemInvitation: (secret: string) => Effect.Effect<typeof IdentityLink.Type, UserFailure>
  readonly logout: () => Effect.Effect<void, UserFailure>
  readonly refreshBrowser: () => Effect.Effect<typeof AuthSession.Type, UserFailure>
  readonly account: (id: string) => Effect.Effect<typeof Account.Type, UserFailure>
  readonly balance: (id: string) => Effect.Effect<typeof Balance.Type, UserFailure>
  readonly credentials: () => Effect.Effect<typeof Credentials.Type, UserFailure>
  readonly issueCredential: (accountId: string, principalId: string, label: string) => Effect.Effect<typeof IssuedCredential.Type, UserFailure>
  readonly rotateCredential: (id: string) => Effect.Effect<typeof IssuedCredential.Type, UserFailure>
  readonly revokeCredential: (id: string) => Effect.Effect<void, UserFailure>
  readonly catalog: () => Effect.Effect<typeof Catalog.Type, UserFailure>
  readonly signerSessions: () => Effect.Effect<typeof SignerSessions.Type, UserFailure>
  readonly createSignerSession: (input: NewSignerSession, idempotencyKey: string) => Effect.Effect<typeof SignerSession.Type, UserFailure>
  readonly refreshSignerSession: (id: string, idempotencyKey: string) => Effect.Effect<typeof SignerSession.Type, UserFailure>
  readonly revokeSignerSession: (id: string) => Effect.Effect<void, UserFailure>
  readonly usage: (accountId: string, cursor?: string) => Effect.Effect<typeof UsagePage.Type, UserFailure>
  readonly charges: (accountId: string, cursor?: string) => Effect.Effect<typeof ChargePage.Type, UserFailure>
}>() {}

const pagePath = (resource: "usage" | "charges", accountId: string, cursor?: string): string => {
  const query = new URLSearchParams({ account_id: accountId, limit: "50" })
  if (cursor !== undefined) query.set("cursor", cursor)
  return `/v1/${resource}?${query.toString()}`
}

export const UserApiLive = Layer.succeed(UserApi, {
  providers: () => json("providers", Providers, "/v1/auth/providers"),
  session: () => json("session", AuthSession, "/v1/auth/session"),
  requestCode: (email) => empty("requestCode", "/v1/auth/email/code", mutation("POST", { email })),
  verifyCode: (email, code) => json("verifyCode", AuthSession, "/v1/auth/email/verify", mutation("POST", { email, code })),
  redeemInvitation: (invitation_secret) => json("redeemInvitation", IdentityLink, "/v1/auth/identity-links", mutation("POST", { invitation_secret })),
  logout: () => empty("logout", "/v1/auth/session", mutation("DELETE")),
  refreshBrowser: () => json("refreshBrowser", AuthSession, "/v1/auth/session/refresh", mutation("POST")),
  account: (id) => json("account", Account, `/v1/accounts/${encodeURIComponent(id)}`),
  balance: (id) => json("balance", Balance, `/v1/balances/${encodeURIComponent(id)}`),
  credentials: () => json("credentials", Credentials, "/v1/credentials"),
  issueCredential: (account_id, principal_id, label) => json("issueCredential", IssuedCredential, "/v1/credentials", mutation("POST", { account_id, principal_id, label })),
  rotateCredential: (id) => json("rotateCredential", IssuedCredential, `/v1/credentials/${encodeURIComponent(id)}/rotate`, mutation("POST")),
  revokeCredential: (id) => empty("revokeCredential", `/v1/credentials/${encodeURIComponent(id)}`, mutation("DELETE")),
  catalog: () => json("catalog", Catalog, "/v1/catalog"),
  signerSessions: () => json("signerSessions", SignerSessions, "/v1/sessions"),
  createSignerSession: (input, idempotencyKey) => json("createSignerSession", SignerSession, "/v1/sessions", {
    ...mutation("POST", input), headers: { ...mutation("POST", input).headers, "Idempotency-Key": idempotencyKey }
  }),
  refreshSignerSession: (id, idempotencyKey) => json("refreshSignerSession", SignerSession, `/v1/sessions/${encodeURIComponent(id)}/refresh`, {
    ...mutation("POST", {}), headers: { ...mutation("POST", {}).headers, "Idempotency-Key": idempotencyKey }
  }),
  revokeSignerSession: (id) => empty("revokeSignerSession", `/v1/sessions/${encodeURIComponent(id)}`, mutation("DELETE")),
  usage: (accountId, cursor) => json("usage", UsagePage, pagePath("usage", accountId, cursor)),
  charges: (accountId, cursor) => json("charges", ChargePage, pagePath("charges", accountId, cursor))
})

const runtime = ManagedRuntime.make(UserApiLive)
export const runUser = <A>(effect: Effect.Effect<A, UserFailure, UserApi>): Promise<A> => runtime.runPromise(effect)
