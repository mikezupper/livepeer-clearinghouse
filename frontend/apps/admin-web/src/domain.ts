import { Schema } from "effect"

const UnsignedIntegerString = Schema.String.pipe(Schema.pattern(/^(0|[1-9][0-9]*)$/u))
const PositiveIntegerString = Schema.String.pipe(Schema.pattern(/^[1-9][0-9]*$/u))
const IsoDateTimeString = Schema.String.pipe(Schema.pattern(
  /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/u
))

export const Providers = Schema.Struct({ providers: Schema.Array(Schema.Literal("email", "google", "github")) })
export const Session = Schema.Struct({
  user_id: Schema.String, account_id: Schema.String, email: Schema.String,
  is_admin: Schema.Boolean, expires_at: IsoDateTimeString
})
export const ExactPrice = Schema.Struct({
  numerator: UnsignedIntegerString, denominator: PositiveIntegerString,
  currency: Schema.String, quantity_unit: Schema.String
})
export const Workload = Schema.Struct({
  id: Schema.String, account_id: Schema.String, capability: Schema.String,
  model: Schema.NullOr(Schema.String), offer_id: Schema.String, quoted_price: ExactPrice,
  status: Schema.Literal("active", "expired", "ended", "revoked"),
  client_reference: Schema.NullOr(Schema.String), runner_session_id: Schema.NullOr(Schema.String),
  manifest_id: Schema.NullOr(Schema.String), payment_session_id: Schema.NullOr(Schema.String),
  created_at: IsoDateTimeString, expires_at: IsoDateTimeString
})
export type Workload = typeof Workload.Type
export const User = Schema.Struct({
  user_id: Schema.String, account_id: Schema.String, email: Schema.String, is_admin: Schema.Boolean
})
export type User = typeof User.Type
const PageFields = { next_cursor: Schema.NullOr(Schema.String) }
export const Users = Schema.Struct({ items: Schema.Array(User), ...PageFields })
export const Workloads = Schema.Struct({ items: Schema.Array(Workload), ...PageFields })
export const Stop = Schema.Struct({ enabled: Schema.Boolean, reason: Schema.String, changed_at: IsoDateTimeString })
export type Stop = typeof Stop.Type
export const Overview = Schema.Struct({
  users: Schema.Number, workloads: Schema.Number, active_workloads: Schema.Number, usage: Schema.Number,
  unmatched_usage: Schema.Number, computed_fee: UnsignedIntegerString, currency: Schema.String,
  global_stop: Stop
})
export type Overview = typeof Overview.Type
