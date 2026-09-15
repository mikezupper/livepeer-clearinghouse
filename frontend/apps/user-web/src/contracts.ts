import { Schema } from "effect"

const UnsignedIntegerString = Schema.String.pipe(Schema.pattern(/^(0|[1-9][0-9]*)$/u))
const PositiveIntegerString = Schema.String.pipe(Schema.pattern(/^[1-9][0-9]*$/u))
const IsoDateTimeString = Schema.String.pipe(Schema.pattern(
  /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/u
))

export const Provider = Schema.Literal("email", "google", "github")
export type Provider = typeof Provider.Type
export const Providers = Schema.Struct({ providers: Schema.Array(Provider) })
export const Session = Schema.Struct({
  user_id: Schema.String, account_id: Schema.String, email: Schema.String,
  is_admin: Schema.Boolean, expires_at: IsoDateTimeString
})
export type Session = typeof Session.Type
export const ExactPrice = Schema.Struct({
  numerator: UnsignedIntegerString, denominator: PositiveIntegerString,
  currency: Schema.String, quantity_unit: Schema.String
})
export const Offer = Schema.Struct({
  id: Schema.String, runner_url: Schema.String,
  orchestrator_address: Schema.NullOr(Schema.String), capability: Schema.String,
  model: Schema.NullOr(Schema.String),
  constraints: Schema.Record({ key: Schema.String, value: Schema.String }),
  price: ExactPrice, observed_at: IsoDateTimeString, expires_at: IsoDateTimeString
})
export type Offer = typeof Offer.Type
const PageFields = { next_cursor: Schema.NullOr(Schema.String) }
export const Offers = Schema.Struct({ items: Schema.Array(Offer), ...PageFields })
export const Credential = Schema.Struct({
  id: Schema.String, name: Schema.String, created_at: IsoDateTimeString,
  revoked_at: Schema.NullOr(IsoDateTimeString)
})
export type Credential = typeof Credential.Type
export const Credentials = Schema.Struct({ items: Schema.Array(Credential), ...PageFields })
export const IssuedCredential = Schema.Struct({
  id: Schema.String, name: Schema.String, token: Schema.String, created_at: IsoDateTimeString
})
export type IssuedCredential = typeof IssuedCredential.Type
export const Workload = Schema.Struct({
  id: Schema.String, account_id: Schema.String, capability: Schema.String,
  model: Schema.NullOr(Schema.String), offer_id: Schema.String,
  quoted_price: ExactPrice, status: Schema.Literal("active", "expired", "ended", "revoked"),
  client_reference: Schema.NullOr(Schema.String), runner_session_id: Schema.NullOr(Schema.String),
  manifest_id: Schema.NullOr(Schema.String), payment_session_id: Schema.NullOr(Schema.String),
  max_spend_wei: Schema.NullOr(PositiveIntegerString),
  created_at: IsoDateTimeString, expires_at: IsoDateTimeString
})
export type Workload = typeof Workload.Type
export const Workloads = Schema.Struct({ items: Schema.Array(Workload), ...PageFields })
export const IssuedWorkload = Workload.pipe(Schema.extend(Schema.Struct({
  token: Schema.String, sdk_token: Schema.String,
  signer_url: Schema.String, discovery_url: Schema.String
})))
export type IssuedWorkload = typeof IssuedWorkload.Type
export const Usage = Schema.Struct({
  id: Schema.String, workload_id: Schema.NullOr(Schema.String), manifest_id: Schema.String,
  payment_session_id: Schema.String, capability: Schema.String, quantity: UnsignedIntegerString,
  quantity_unit: Schema.String, computed_fee: UnsignedIntegerString, currency: Schema.String,
  ticket_count: Schema.Number, sequence_number: Schema.Number.pipe(Schema.int(), Schema.nonNegative()),
  occurred_at: IsoDateTimeString,
  status: Schema.Literal("matched", "unmatched")
})
export type Usage = typeof Usage.Type
export const UsageItems = Schema.Struct({ items: Schema.Array(Usage), ...PageFields })
export const Cost = Schema.Struct({
  workload: Workload, measured_quantity: UnsignedIntegerString, measured_unit: Schema.String,
  quoted_fee: UnsignedIntegerString, computed_fee: UnsignedIntegerString, currency: Schema.String,
  event_count: Schema.Number, spend_ceiling: Schema.NullOr(PositiveIntegerString),
  authorized_fee: UnsignedIntegerString, pending_fee: UnsignedIntegerString,
  remaining_spend: Schema.NullOr(UnsignedIntegerString)
})
export type Cost = typeof Cost.Type
export const Costs = Schema.Struct({ items: Schema.Array(Cost), ...PageFields })
export const Summary = Schema.Struct({
  offers: Schema.Number.pipe(Schema.int(), Schema.nonNegative()),
  credentials: Schema.Number.pipe(Schema.int(), Schema.nonNegative()),
  workloads: Schema.Number.pipe(Schema.int(), Schema.nonNegative()),
  active_workloads: Schema.Number.pipe(Schema.int(), Schema.nonNegative()),
  usage_events: Schema.Number.pipe(Schema.int(), Schema.nonNegative()),
  computed_fee: UnsignedIntegerString,
  currency: Schema.String
})
export type Summary = typeof Summary.Type
