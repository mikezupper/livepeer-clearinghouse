import { OpaqueId, UnsignedInteger } from "@livepeer/clearinghouse-contracts"
import { Schema } from "effect"

export const Provider = Schema.Literal("email", "google", "github")
export type Provider = typeof Provider.Type

export const Providers = Schema.Struct({ providers: Schema.Array(Provider) })
const PositiveInteger = Schema.String.pipe(Schema.pattern(/^[1-9][0-9]*$/u))
const SignedInteger = Schema.String.pipe(Schema.pattern(/^-?(0|[1-9][0-9]*)$/u))
const Unit = Schema.String.pipe(Schema.pattern(/^[a-z][a-z0-9_]{1,31}$/u))
const SourceInstant = Schema.String.pipe(Schema.pattern(/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$/u))

export const AuthSession = Schema.Struct({
  principal_id: OpaqueId,
  tenant_id: Schema.optional(OpaqueId),
  account_id: Schema.optional(OpaqueId),
  roles: Schema.Array(Schema.Literal("operator", "tenant_admin", "credential_holder")),
  expires_at: Schema.DateTimeUtc
})
export type AuthSession = typeof AuthSession.Type

const Money = Schema.Struct({ amount: SignedInteger, unit: Unit })
const Lease = Schema.Struct({
  id: OpaqueId,
  cap: UnsignedInteger,
  available: UnsignedInteger,
  pending: UnsignedInteger,
  settled: UnsignedInteger,
  unit: Unit,
  expires_at: Schema.DateTimeUtc
})

export const Account = Schema.Struct({
  id: OpaqueId,
  tenant_id: OpaqueId,
  display_name: Schema.String,
  unit: Unit,
  exposure_cap: UnsignedInteger,
  status: Schema.Literal("active", "suspended"),
  created_at: Schema.DateTimeUtc
})
export type Account = typeof Account.Type

export const Balance = Schema.Struct({
  account_id: OpaqueId,
  posted: Money,
  open_lease_exposure: Money,
  available: Money
})
export type Balance = typeof Balance.Type

export const Credential = Schema.Struct({
  id: OpaqueId,
  account_id: OpaqueId,
  principal_id: OpaqueId,
  prefix: Schema.String,
  label: Schema.String,
  status: Schema.Literal("active", "revoked", "expired"),
  created_at: Schema.DateTimeUtc
})
export type Credential = typeof Credential.Type
export const Credentials = Schema.Array(Credential)
export const IssuedCredential = Schema.Struct({ credential: Credential, secret: Schema.String.pipe(Schema.minLength(32)) })

const ExactRate = Schema.Struct({
  numerator: UnsignedInteger,
  denominator: PositiveInteger,
  charge_unit: Unit,
  quantity_unit: Schema.Literal("fixed", "seconds", "pixels", "720p-pixel-seconds", "wei")
})
export const CatalogEntry = Schema.Struct({
  capability: Schema.String.pipe(Schema.minLength(1), Schema.maxLength(128)),
  model: Schema.NullOr(Schema.String),
  rate: ExactRate,
  available: Schema.Boolean
})
export type CatalogEntry = typeof CatalogEntry.Type
export const Catalog = Schema.Array(CatalogEntry)

export const SignerSession = Schema.Struct({
  id: OpaqueId,
  token: Schema.String.pipe(Schema.minLength(32)),
  signer_url: Schema.String,
  discovery_url: Schema.String,
  expires_at: Schema.DateTimeUtc,
  lease: Lease
})
export type SignerSession = typeof SignerSession.Type
export const SignerSessionMetadata = SignerSession.pipe(Schema.omit("token"))
export type SignerSessionMetadata = typeof SignerSessionMetadata.Type
export const SignerSessions = Schema.Struct({ items: Schema.Array(SignerSessionMetadata) })

const Cursor = Schema.String.pipe(
  Schema.minLength(1),
  Schema.maxLength(512),
  Schema.pattern(/^[A-Za-z0-9_-]+$/u)
)
const Page = Schema.Struct({ next_cursor: Schema.NullOr(Cursor) })
const PriceSnapshot = Schema.Struct({
  rate_numerator: UnsignedInteger,
  rate_denominator: PositiveInteger,
  charge_unit: Unit,
  quantity_unit: Schema.Literal("fixed", "seconds", "pixels", "720p-pixel-seconds", "wei"),
  source: Schema.Literal("rate_card", "signer"),
  source_id: Schema.optional(OpaqueId),
  source_version: Schema.String
})
export const Usage = Schema.Struct({
  schema_version: Schema.Literal("1.0"),
  event_id: OpaqueId,
  reservation_id: OpaqueId,
  lease_id: OpaqueId,
  tenant_id: OpaqueId,
  account_id: OpaqueId,
  principal_id: OpaqueId,
  job_id: Schema.optional(OpaqueId),
  manifest_id: Schema.optional(Schema.String),
  capability: Schema.String,
  model: Schema.optional(Schema.String),
  quantity: Schema.Struct({ value: PositiveInteger, unit: Schema.Literal("fixed", "seconds", "pixels", "720p-pixel-seconds", "wei") }),
  price_snapshot: PriceSnapshot,
  producer: Schema.Struct({ id: OpaqueId, kind: Schema.Literal("signer", "collector"), software: Schema.String, software_version: Schema.String }),
  occurred_at: SourceInstant,
  source: Schema.Struct({
    kind: Schema.Literal("go_livepeer_create_signed_ticket", "signer_sequence_reconciliation", "direct"),
    event_id: Schema.String,
    sequence_number: Schema.optional(Schema.String),
    confirmation: Schema.Literal("kafka", "subsequent_signed_state", "direct"),
    signed_current_time: SourceInstant,
    signed_current_time_unix_ns: SignedInteger,
    ticket_count: Schema.optional(Schema.String)
  })
})
export type Usage = typeof Usage.Type
export const UsagePage = Schema.Struct({ items: Schema.Array(Usage), page: Page })

export const Charge = Schema.Struct({
  id: OpaqueId,
  usage_event_id: OpaqueId,
  reservation_id: OpaqueId,
  lease_id: OpaqueId,
  tenant_id: OpaqueId,
  account_id: OpaqueId,
  amount: Schema.Struct({ value: UnsignedInteger, unit: Schema.String }),
  price_snapshot: Schema.Struct({
    rate_card_id: OpaqueId,
    rate_numerator: PositiveInteger,
    rate_denominator: PositiveInteger,
    quantity_unit: Schema.Literal("fixed", "seconds", "720p-pixel-seconds")
  }),
  created_at: Schema.DateTimeUtc
})
export type Charge = typeof Charge.Type
export const ChargePage = Schema.Struct({ items: Schema.Array(Charge), page: Page })

export const IdentityLink = Schema.Struct({
  id: OpaqueId,
  provider: Provider,
  principal_id: OpaqueId,
  tenant_id: OpaqueId,
  linked_at: Schema.DateTimeUtc
})

export interface NewSignerSession {
  readonly capability: string
  readonly model?: string
  readonly app: string
  readonly requested_cap: string
  readonly unit: "wei"
  readonly ttl_seconds: number
}
