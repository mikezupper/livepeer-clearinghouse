import { Data, Schema } from "effect"

const options = { errors: "all", onExcessProperty: "error" } as const
const Id = Schema.String.pipe(
  Schema.pattern(/^[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$/u),
  Schema.brand("AdminId")
)
const Integer = Schema.BigInt
const Status = Schema.Literal("active", "suspended")
const BoundedText = Schema.String.pipe(Schema.minLength(1), Schema.maxLength(1000))
const Unit = Schema.String.pipe(Schema.pattern(/^[a-z][a-z0-9_]{1,31}$/u))
const Unsigned = Schema.String.pipe(Schema.pattern(/^(0|[1-9][0-9]{0,77})$/u))
const Positive = Schema.String.pipe(Schema.pattern(/^[1-9][0-9]{0,77}$/u))
const Signed = Schema.String.pipe(Schema.pattern(/^-?(0|[1-9][0-9]{0,77})$/u))
const PageMeta = Schema.Struct({ next_cursor: Schema.NullOr(Schema.String) })
const page = <A, I, R>(item: Schema.Schema<A, I, R>) => Schema.Struct({
  items: Schema.Array(item),
  page: PageMeta
})

export class Tenant extends Schema.Class<Tenant>("Tenant")({
  id: Id,
  display_name: Schema.NonEmptyTrimmedString,
  status: Status,
  created_at: Schema.DateTimeUtc
}) {}

export class Account extends Schema.Class<Account>("Account")({
  id: Id,
  tenant_id: Id,
  display_name: Schema.NonEmptyTrimmedString,
  unit: Schema.String,
  exposure_cap: Integer,
  status: Status,
  created_at: Schema.DateTimeUtc
}) {}

export class AuthSession extends Schema.Class<AuthSession>("AuthSession")({
  principal_id: Id,
  tenant_id: Schema.optional(Id),
  account_id: Schema.optional(Id),
  roles: Schema.Array(Schema.Literal("operator", "tenant_admin", "credential_holder")),
  expires_at: Schema.DateTimeUtc
}) {}
export const AuthProviders = Schema.Struct({
  providers: Schema.Array(Schema.Literal("email", "google", "github"))
})
export const EmailAddress = Schema.String.pipe(
  Schema.minLength(3),
  Schema.maxLength(320),
  Schema.pattern(/^[^\s@]+@[^\s@]+$/u)
)
export const EmailCode = Schema.String.pipe(Schema.pattern(/^[0-9]{6}$/u))
export const InvitationRequest = Schema.Struct({
  principal_id: Id,
  source_principal_id: Id,
  reason: BoundedText
})
export class IssuedInvitation extends Schema.Class<IssuedInvitation>("IssuedInvitation")({
  id: Id,
  source_principal_id: Id,
  principal_id: Id,
  tenant_id: Id,
  expires_at: Schema.DateTimeUtc,
  created_at: Schema.DateTimeUtc,
  invitation_secret: Schema.String.pipe(
    Schema.minLength(50),
    Schema.maxLength(128),
    Schema.pattern(/^och_inv_[A-Za-z0-9_-]+$/u)
  )
}) {}

export class Principal extends Schema.Class<Principal>("Principal")({
  id: Id,
  tenant_id: Schema.NullOr(Id),
  account_id: Schema.NullOr(Id),
  display_name: Schema.NullOr(Schema.String),
  roles: Schema.Array(Schema.Literal("operator", "tenant_admin", "credential_holder")),
  status: Status,
  created_at: Schema.DateTimeUtc
}) {}

export const Money = Schema.Struct({ amount: Integer, unit: Schema.String })
export class Grant extends Schema.Class<Grant>("Grant")({
  id: Id,
  account_id: Id,
  kind: Schema.Literal("credit", "debit", "adjustment"),
  amount: Money,
  reason: Schema.String,
  external_reference: Schema.NullOr(Schema.String),
  actor_id: Id,
  created_at: Schema.DateTimeUtc
}) {}

export class Balance extends Schema.Class<Balance>("Balance")({
  account_id: Id,
  posted: Money,
  open_lease_exposure: Money,
  available: Money
}) {}

export class Lease extends Schema.Class<Lease>("Lease")({
  id: Id,
  cap: Integer,
  available: Integer,
  pending: Integer,
  settled: Integer,
  unit: Schema.String,
  expires_at: Schema.DateTimeUtc
}) {}

const ExactRate = Schema.Struct({
  rate_numerator: Integer,
  rate_denominator: Integer,
  charge_unit: Schema.String,
  quantity_unit: Schema.String,
  source: Schema.Literal("rate_card", "signer"),
  source_id: Schema.optional(Id),
  source_version: Schema.String
})

export class Usage extends Schema.Class<Usage>("Usage")({
  schema_version: Schema.Literal("1.0"),
  event_id: Id,
  reservation_id: Id,
  lease_id: Id,
  tenant_id: Id,
  account_id: Id,
  principal_id: Id,
  job_id: Schema.optional(Id),
  manifest_id: Schema.optional(Schema.String),
  capability: Schema.String,
  model: Schema.optional(Schema.String),
  quantity: Schema.Struct({ value: Integer, unit: Schema.String }),
  price_snapshot: ExactRate,
  producer: Schema.Struct({
    id: Id,
    kind: Schema.Literal("signer", "collector"),
    software: Schema.String,
    software_version: Schema.String
  }),
  occurred_at: Schema.String.pipe(
    Schema.pattern(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/u)
  ),
  source: Schema.Struct({
    kind: Schema.Literal(
      "go_livepeer_create_signed_ticket",
      "signer_sequence_reconciliation",
      "direct"
    ),
    event_id: Schema.String,
    sequence_number: Schema.optional(Integer),
    confirmation: Schema.Literal("kafka", "subsequent_signed_state", "direct"),
    signed_current_time: Schema.String,
    signed_current_time_unix_ns: Integer,
    ticket_count: Schema.optional(Integer)
  })
}) {}

export class Charge extends Schema.Class<Charge>("Charge")({
  id: Id,
  usage_event_id: Id,
  reservation_id: Id,
  lease_id: Id,
  tenant_id: Id,
  account_id: Id,
  amount: Schema.Struct({ value: Integer, unit: Schema.String }),
  price_snapshot: Schema.Struct({
    rate_card_id: Id,
    rate_numerator: Integer,
    rate_denominator: Integer,
    quantity_unit: Schema.String
  }),
  created_at: Schema.DateTimeUtc
}) {}

export class OpenReservation extends Schema.Class<OpenReservation>("OpenReservation")({
  id: Id,
  lease_id: Id,
  tenant_id: Id,
  account_id: Id,
  status: Schema.Literal("pending", "unresolved", "quarantined"),
  reserved_amount: Schema.Struct({ value: Integer, unit: Schema.String }),
  sequence_number: Integer,
  signer_confirmed_at: Schema.NullOr(Schema.DateTimeUtc),
  created_at: Schema.DateTimeUtc
}) {}

export class RateCard extends Schema.Class<RateCard>("RateCard")({
  id: Id,
  version: Integer,
  capability: Schema.String,
  model: Schema.NullOr(Schema.String),
  rate: Schema.Struct({
    numerator: Integer,
    denominator: Integer,
    charge_unit: Schema.String,
    quantity_unit: Schema.String
  }),
  effective_at: Schema.DateTimeUtc,
  created_at: Schema.DateTimeUtc
}) {}

export class MeteringHealth extends Schema.Class<MeteringHealth>("MeteringHealth")({
  status: Schema.Literal("ready", "degraded"),
  open_cases: Schema.NonNegativeInt,
  quarantined: Schema.NonNegativeInt,
  unresolved: Schema.NonNegativeInt,
  global_exposure_cap: Integer,
  global_open_exposure: Integer,
  last_checkpoint_at: Schema.NullOr(Schema.String),
  last_heartbeat_at: Schema.NullOr(Schema.String)
}) {}

export class ReconciliationCase extends Schema.Class<ReconciliationCase>("ReconciliationCase")({
  id: Id,
  reservation_id: Schema.NullOr(Id),
  tenant_id: Schema.NullOr(Id),
  account_id: Schema.NullOr(Id),
  kind: Schema.String,
  status: Schema.Literal("open", "resolved"),
  reason: Schema.String,
  created_at: Schema.DateTimeUtc,
  resolved_at: Schema.NullOr(Schema.DateTimeUtc)
}) {}

export class AuditEvent extends Schema.Class<AuditEvent>("AuditEvent")({
  id: Id,
  actor_id: Id,
  action: Schema.String,
  target_id: Schema.String,
  reason: Schema.String,
  request_id: Schema.String,
  occurred_at: Schema.DateTimeUtc
}) {}

export class KillSwitch extends Schema.Class<KillSwitch>("KillSwitch")({
  enabled: Schema.Boolean,
  reason: Schema.String,
  changed_at: Schema.DateTimeUtc,
  actor_id: Schema.NullOr(Id)
}) {}

export const AdapterManifest = Schema.Struct({
  manifest_version: Schema.Literal("1.0"),
  name: Schema.String,
  version: Schema.String,
  description: Schema.optional(Schema.String),
  source: Schema.Literal("builtin", "python_entry_point", "http_bridge"),
  builtin: Schema.optional(Schema.Struct({ selector: Schema.String })),
  python_entry_point: Schema.optional(Schema.Struct({ group: Schema.String, name: Schema.String,
    distribution: Schema.optional(Schema.String) })),
  http_bridge: Schema.optional(Schema.Struct({ base_url: Schema.String,
    authentication: Schema.Literal("bearer", "mtls"), timeout_ms: Schema.Int })),
  ports: Schema.Array(Schema.Struct({ name: Schema.String, contract_version: Schema.String,
    capabilities: Schema.Array(Schema.String) })),
  configuration_schema: Schema.optional(Schema.Record({ key: Schema.String, value: Schema.Unknown }))
})

export const Overview = Schema.Struct({
  session: AuthSession,
  tenants: Schema.Array(Tenant),
  accounts: Schema.Array(Account),
  principals: Schema.Array(Principal),
  balances: Schema.Array(Balance),
  grants: Schema.Array(Grant),
  leases: Schema.Array(Lease),
  usage: Schema.Array(Usage),
  charges: Schema.Array(Charge),
  reservations: Schema.Array(OpenReservation),
  rates: Schema.Array(RateCard),
  metering: Schema.NullOr(MeteringHealth),
  reconciliation: Schema.Array(ReconciliationCase),
  audit: Schema.Array(AuditEvent),
  adapters: Schema.NullOr(Schema.Array(AdapterManifest)),
  killSwitch: Schema.NullOr(KillSwitch)
})
export type Overview = typeof Overview.Type

export const decoders = {
  tenants: page(Tenant),
  accounts: page(Account),
  session: AuthSession,
  principals: Schema.Array(Principal),
  balance: Balance,
  grants: page(Grant),
  leases: Schema.Struct({ items: Schema.Array(Lease) }),
  usage: page(Usage),
  charges: page(Charge),
  reservations: page(OpenReservation),
  rates: Schema.Array(RateCard),
  metering: MeteringHealth,
  reconciliation: page(ReconciliationCase),
  audit: page(AuditEvent),
  adapters: Schema.Array(AdapterManifest),
  killSwitch: KillSwitch,
  invitation: IssuedInvitation,
  tenant: Schema.decodeUnknown(Tenant, options),
  account: Schema.decodeUnknown(Account, options),
  grant: Schema.decodeUnknown(Grant, options),
  rate: Schema.decodeUnknown(RateCard, options)
}

const MutationBase = {
  idempotencyKey: Schema.optional(Schema.String.pipe(Schema.minLength(16), Schema.maxLength(256)))
}
export const MutationCommand = Schema.Union(
  Schema.Struct({ ...MutationBase, path: Schema.Literal("/v1/tenants"), method: Schema.Literal("POST"), body: Schema.Struct({ display_name: Schema.String.pipe(Schema.minLength(1), Schema.maxLength(200)) }) }),
  Schema.Struct({ ...MutationBase, path: Schema.Literal("/v1/accounts"), method: Schema.Literal("POST"), body: Schema.Struct({ tenant_id: Id, display_name: Schema.String.pipe(Schema.minLength(1), Schema.maxLength(200)), unit: Unit, exposure_cap: Unsigned }) }),
  Schema.Struct({ ...MutationBase, path: Schema.Literal("/v1/principals"), method: Schema.Literal("POST"), body: Schema.Struct({ tenant_id: Schema.NullOr(Id), account_id: Schema.NullOr(Id), display_name: Schema.NullOr(Schema.String.pipe(Schema.maxLength(200))), roles: Schema.Array(Schema.Literal("operator", "tenant_admin", "credential_holder")) }) }),
  Schema.Struct({ ...MutationBase, path: Schema.Literal("/v1/grants"), method: Schema.Literal("POST"), body: Schema.Struct({ account_id: Id, kind: Schema.Literal("credit", "debit", "adjustment"), amount: Schema.Struct({ amount: Signed, unit: Unit }), reason: BoundedText, external_reference: Schema.NullOr(Schema.String.pipe(Schema.maxLength(256))) }) }),
  Schema.Struct({ ...MutationBase, path: Schema.Literal("/v1/rate-cards"), method: Schema.Literal("POST"), body: Schema.Struct({ capability: Schema.String.pipe(Schema.minLength(1), Schema.maxLength(128)), model: Schema.NullOr(Schema.String.pipe(Schema.maxLength(512))), rate: Schema.Struct({ numerator: Unsigned, denominator: Positive, charge_unit: Unit, quantity_unit: Schema.Literal("fixed", "seconds", "pixels", "720p-pixel-seconds", "wei") }), effective_at: Schema.String.pipe(Schema.pattern(/^\d{4}-\d{2}-\d{2}T/u)) }) }),
  Schema.Struct({ ...MutationBase, path: Schema.Literal("/v1/operations/kill-switch"), method: Schema.Literal("PUT"), body: Schema.Struct({ enabled: Schema.Boolean, reason: BoundedText }) }),
  Schema.Struct({ ...MutationBase, path: Schema.String.pipe(Schema.pattern(/^\/v1\/tenants\/[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$/u)), method: Schema.Literal("PATCH"), body: Schema.Struct({ status: Status, reason: BoundedText }) }),
  Schema.Struct({ ...MutationBase, path: Schema.String.pipe(Schema.pattern(/^\/v1\/accounts\/[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$/u)), method: Schema.Literal("PATCH"), body: Schema.Struct({ exposure_cap: Unsigned, status: Status, reason: BoundedText }) }),
  Schema.Struct({ ...MutationBase, path: Schema.String.pipe(Schema.pattern(/^\/v1\/principals\/[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$/u)), method: Schema.Literal("PATCH"), body: Schema.Struct({ status: Status, roles: Schema.Array(Schema.Literal("operator", "tenant_admin", "credential_holder")), reason: BoundedText }) }),
  Schema.Struct({ ...MutationBase, path: Schema.String.pipe(Schema.pattern(/^\/v1\/accounts\/[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}\/capabilities$/u)), method: Schema.Literal("PUT"), body: Schema.Struct({ capability: Schema.String.pipe(Schema.minLength(1), Schema.maxLength(128)), model: Schema.NullOr(Schema.String.pipe(Schema.maxLength(512))), allowed: Schema.Boolean, reason: BoundedText }) })
)
export type MutationCommand = typeof MutationCommand.Type

export class TransportFailure extends Data.TaggedError("TransportFailure")<{
  readonly operation: string
  readonly cause: unknown
}> {}
export class InvalidPayload extends Data.TaggedError("InvalidPayload")<{
  readonly operation: string
  readonly cause: unknown
}> {}
export class ApiProblem extends Data.TaggedError("ApiProblem")<{
  readonly operation: string
  readonly status: number
  readonly title: string
}> {}

export type AdminFailure = TransportFailure | InvalidPayload | ApiProblem
export type ResourceState = Data.TaggedEnum<{
  Loading: Record<never, never>
  Ready: { readonly value: Overview }
  Failed: { readonly message: string; readonly status: number | null }
}>
export const ResourceState = Data.taggedEnum<ResourceState>()
