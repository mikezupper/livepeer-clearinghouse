import { Context, Effect, Schema } from "effect"
import {
  ApiProblem,
  type AdminFailure,
  AuthProviders,
  EmailAddress,
  EmailCode,
  InvitationRequest,
  InvalidPayload,
  MutationCommand,
  type Overview,
  TransportFailure,
  decoders
} from "./domain.js"

export interface Mutation {
  readonly path: string
  readonly method: "POST" | "PUT" | "PATCH" | "DELETE"
  readonly body: Readonly<Record<string, unknown>>
  readonly idempotencyKey?: string
}

export interface AdminApiShape {
  readonly overview: Effect.Effect<Overview, AdminFailure>
  readonly providers: Effect.Effect<ReadonlyArray<"email" | "google" | "github">, AdminFailure>
  readonly requestEmailCode: (email: string) => Effect.Effect<void, AdminFailure>
  readonly verifyEmailCode: (email: string, code: string) => Effect.Effect<void, AdminFailure>
  readonly refreshSession: Effect.Effect<void, AdminFailure>
  readonly logout: Effect.Effect<void, AdminFailure>
  readonly issueInvitation: (
    principalId: string,
    sourcePrincipalId: string,
    reason: string
  ) => Effect.Effect<string, AdminFailure>
  readonly mutate: (command: Mutation) => Effect.Effect<void, AdminFailure>
}

export class AdminApi extends Context.Tag("clearinghouse/AdminApi")<AdminApi, AdminApiShape>() {}

const csrfToken = (): string => document.cookie
  .split("; ")
  .find((entry) => entry.startsWith("och_csrf="))
  ?.slice("och_csrf=".length) ?? ""

const request = (
  operation: string,
  path: string,
  init?: RequestInit
): Effect.Effect<unknown, AdminFailure> => Effect.tryPromise({
  try: (signal) => fetch(path, { ...init, credentials: "same-origin", signal }),
  catch: (cause) => new TransportFailure({ operation, cause })
}).pipe(
  Effect.flatMap((response): Effect.Effect<unknown, AdminFailure> => response.ok
    ? Effect.tryPromise({
      try: () => response.status === 202 || response.status === 204
        ? Promise.resolve(undefined)
        : response.json(),
      catch: (cause) => new InvalidPayload({ operation, cause })
    })
    : Effect.fail(new ApiProblem({
      operation,
      status: response.status,
      title: response.status === 401 ? "Sign in required" : "Request was not accepted"
    })))
)

const decoded = <A, I>(
  operation: string,
  effect: Effect.Effect<unknown, AdminFailure>,
  schema: Schema.Schema<A, I, never>
): Effect.Effect<A, AdminFailure> => effect.pipe(
  Effect.flatMap((input) => Schema.decodeUnknown(schema, {
    errors: "all",
    onExcessProperty: "error"
  })(input).pipe(
    Effect.mapError((cause) => new InvalidPayload({ operation, cause }))
  ))
)

interface Page<A> {
  readonly items: ReadonlyArray<A>
  readonly page: { readonly next_cursor: string | null }
}

const collectPages = <A, I>(
  operation: string,
  path: string,
  schema: Schema.Schema<Page<A>, I, never>
): Effect.Effect<ReadonlyArray<A>, AdminFailure> => {
  const loop = (
    cursor: string | null,
    items: ReadonlyArray<A>
  ): Effect.Effect<ReadonlyArray<A>, AdminFailure> => {
    const nextPath = cursor === null ? path : `${path}&cursor=${encodeURIComponent(cursor)}`
    return decoded(operation, request(operation, nextPath), schema).pipe(
      Effect.flatMap((result) => result.page.next_cursor === null
        ? Effect.succeed([...items, ...result.items])
        : Effect.suspend(() => loop(result.page.next_cursor, [...items, ...result.items])))
    )
  }
  return loop(null, [])
}

export const AdminApiLive: AdminApiShape = {
  providers: decoded(
    "authentication providers",
    request("authentication providers", "/v1/auth/providers"),
    AuthProviders
  ).pipe(Effect.map((value) => value.providers)),
  requestEmailCode: (email) => Schema.decodeUnknown(EmailAddress)(email).pipe(
    Effect.mapError((cause) => new InvalidPayload({ operation: "request email code", cause })),
    Effect.flatMap((validEmail) => request("request email code", "/v1/auth/email/code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: validEmail })
    })),
    Effect.asVoid
  ),
  verifyEmailCode: (email, code) => Effect.all({
    email: Schema.decodeUnknown(EmailAddress)(email),
    code: Schema.decodeUnknown(EmailCode)(code)
  }).pipe(
    Effect.mapError((cause) => new InvalidPayload({ operation: "verify email code", cause })),
    Effect.flatMap((valid) => decoded(
      "verify email code",
      request("verify email code", "/v1/auth/email/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(valid)
      }),
      decoders.session
    )),
    Effect.asVoid
  ),
  refreshSession: Effect.suspend(() => request("refresh session", "/v1/auth/session/refresh", {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken() }
  }).pipe(Effect.asVoid)),
  logout: Effect.suspend(() => request("sign out", "/v1/auth/session", {
    method: "DELETE",
    headers: { "X-CSRF-Token": csrfToken() }
  }).pipe(Effect.asVoid)),
  issueInvitation: (principalId, sourcePrincipalId, reason) => Schema.decodeUnknown(InvitationRequest)({
    principal_id: principalId,
    source_principal_id: sourcePrincipalId,
    reason
  }).pipe(
    Effect.mapError((cause) => new InvalidPayload({ operation: "issue identity invitation", cause })),
    Effect.flatMap((valid) => decoded(
      "issue identity invitation",
      request("issue identity invitation", `/v1/principals/${valid.principal_id}/identity-invitations`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ source_principal_id: valid.source_principal_id, reason: valid.reason })
      }),
      decoders.invitation
    )),
    Effect.map((invitation) => invitation.invitation_secret)
  ),
  overview: Effect.gen(function* () {
    const session = yield* decoded(
      "current session",
      request("current session", "/v1/auth/session"),
      decoders.session
    )
    const operator = session.roles.includes("operator")
    if (!operator && !session.roles.includes("tenant_admin")) {
      return yield* Effect.fail(new ApiProblem({
        operation: "load administration",
        status: 403,
        title: "Administrator role required"
      }))
    }
    const common = yield* Effect.all({
      tenants: collectPages("list tenants", "/v1/tenants?limit=50", decoders.tenants),
      accounts: collectPages("list accounts", "/v1/accounts?limit=100", decoders.accounts),
      principals: decoded("list principals", request("list principals", "/v1/principals"), decoders.principals),
      grants: collectPages("list grants", "/v1/grants?limit=100", decoders.grants),
      leases: decoded("list leases", request("list leases", "/v1/leases"), decoders.leases),
      usage: collectPages("list usage", "/v1/usage?limit=100", decoders.usage),
      charges: collectPages("list charges", "/v1/charges?limit=100", decoders.charges),
      reservations: collectPages("open reservations", "/v1/open-reservations?limit=100", decoders.reservations),
      rates: decoded("list rate cards", request("list rate cards", "/v1/rate-cards"), decoders.rates),
      reconciliation: collectPages("reconciliation", "/v1/operations/reconciliation?limit=100", decoders.reconciliation),
      audit: collectPages("audit", "/v1/operations/audit-events?limit=100", decoders.audit)
    }, { concurrency: 4 })
    const balances = yield* Effect.forEach(
      common.accounts,
      (account) => decoded(
        "account balance",
        request("account balance", `/v1/balances/${account.id}`),
        decoders.balance
      ),
      { concurrency: 4 }
    )
    const operations = operator
      ? yield* Effect.all({
        metering: decoded("metering health", request("metering health", "/v1/operations/metering-health"), decoders.metering),
        adapters: decoded("adapters", request("adapters", "/v1/operations/adapters"), decoders.adapters),
        killSwitch: decoded("kill switch", request("kill switch", "/v1/operations/kill-switch"), decoders.killSwitch)
      }, { concurrency: 4 })
      : { metering: null, adapters: null, killSwitch: null }
    return {
      session,
      tenants: common.tenants,
      accounts: common.accounts,
      principals: common.principals,
      balances,
      grants: common.grants,
      leases: common.leases.items,
      usage: common.usage,
      charges: common.charges,
      reservations: common.reservations,
      rates: common.rates,
      metering: operations.metering,
      reconciliation: common.reconciliation,
      audit: common.audit,
      adapters: operations.adapters,
      killSwitch: operations.killSwitch
    }
  }),
  mutate: (command) => Schema.decodeUnknown(MutationCommand, {
    errors: "all",
    onExcessProperty: "error"
  })(command).pipe(
    Effect.mapError((cause) => new InvalidPayload({ operation: command.path, cause })),
    Effect.flatMap((valid) => request(valid.path, valid.path, {
      method: valid.method,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken(),
        ...(valid.idempotencyKey === undefined
          ? {}
          : { "Idempotency-Key": valid.idempotencyKey })
      },
      body: JSON.stringify(valid.body)
    })),
    Effect.asVoid
  )
}

export const loadOverview = AdminApi.pipe(Effect.flatMap((api) => api.overview))
export const applyMutation = (command: Mutation) => AdminApi.pipe(
  Effect.flatMap((api) => api.mutate(command))
)

export const reasonFromFailure = (failure: AdminFailure): string => failure._tag === "ApiProblem"
  ? `${failure.title} (${failure.status})`
  : failure._tag === "InvalidPayload"
    ? "The server returned an incompatible response."
    : "The clearinghouse is unavailable."
