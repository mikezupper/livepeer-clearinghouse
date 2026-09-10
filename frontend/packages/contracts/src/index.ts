import { Schema } from "effect"

const boundaryOptions = {
  errors: "all",
  onExcessProperty: "error"
} as const

export const OpaqueId = Schema.String.pipe(
  Schema.pattern(/^[a-z][a-z0-9_]{1,31}_[A-Za-z0-9_-]{8,128}$/u),
  Schema.brand("OpaqueId")
)
export type OpaqueId = typeof OpaqueId.Type

export const UnsignedInteger = Schema.String.pipe(
  Schema.pattern(/^(0|[1-9][0-9]*)$/u),
  Schema.brand("UnsignedInteger")
)
export type UnsignedInteger = typeof UnsignedInteger.Type

export const HealthCheckStatus = Schema.Literal("ok", "unavailable", "disabled")

export const Health = Schema.Struct({
  status: Schema.Literal("ok", "unavailable"),
  checks: Schema.optional(Schema.Record({
    key: Schema.String,
    value: HealthCheckStatus
  }))
})
export type Health = typeof Health.Type

export const Account = Schema.Struct({
  id: OpaqueId,
  tenant_id: OpaqueId,
  display_name: Schema.String,
  unit: Schema.String,
  exposure_cap: UnsignedInteger,
  status: Schema.Literal("active", "suspended"),
  created_at: Schema.DateTimeUtc
})
export type Account = typeof Account.Type

export const decodeHealth = Schema.decodeUnknown(Health, boundaryOptions)
export const decodeAccount = Schema.decodeUnknown(Account, boundaryOptions)

export const isHealthAvailable = (health: Health): boolean => health.status === "ok"
  && Object.values(health.checks ?? {}).every((status) => status !== "unavailable")
