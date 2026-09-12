import { Option } from "effect"
import type { ExactPrice } from "./contracts.js"

export interface QuoteInputs {
  readonly executions: string
  readonly duration: string
  readonly width: string
  readonly height: string
  readonly frames: string
  readonly fps: string
}

export type QuoteEstimate =
  | { readonly _tag: "Ready"; readonly cost: bigint; readonly quantity: string; readonly quantityUnit: string }
  | { readonly _tag: "Invalid"; readonly message: string }
  | { readonly _tag: "Unsupported"; readonly unit: string }

type Fraction = { readonly numerator: bigint; readonly denominator: bigint }

const integerPattern = /^[1-9][0-9]*$/u
const decimalPattern = /^(?:0|[1-9][0-9]*)(?:\.([0-9]{1,9}))?$/u
const positiveInteger = (value: string): Option.Option<bigint> =>
  integerPattern.test(value) ? Option.some(BigInt(value)) : Option.none()
const positiveDecimal = (value: string): Option.Option<Fraction> => {
  const match = decimalPattern.exec(value)
  if (match === null) return Option.none()
  const decimal = match[1] ?? ""
  const denominator = 10n ** BigInt(decimal.length)
  const numerator = BigInt(value.replace(".", ""))
  return numerator > 0n ? Option.some({ numerator, denominator }) : Option.none()
}
const ceilingDivide = (numerator: bigint, denominator: bigint): bigint =>
  (numerator + denominator - 1n) / denominator
const decimal = ({ numerator, denominator }: Fraction): string => {
  const whole = numerator / denominator
  const remainder = numerator % denominator
  if (remainder === 0n) return whole.toString()
  const places = denominator.toString().length - 1
  const fraction = remainder.toString().padStart(places, "0").replace(/0+$/u, "")
  return `${whole}.${fraction}`
}
const invalid = (message: string): QuoteEstimate => ({ _tag: "Invalid", message })

export const estimateQuote = (price: typeof ExactPrice.Type, inputs: QuoteInputs): QuoteEstimate => {
  const executions = positiveInteger(inputs.executions)
  if (Option.isNone(executions)) return invalid("Enter at least one execution.")
  const unit = price.quantity_unit.trim().toLowerCase()
  let quantity: Fraction
  let quantityUnit: string

  if (unit === "fixed") {
    quantity = { numerator: executions.value, denominator: 1n }
    quantityUnit = "fixed execution"
  } else if (unit === "seconds" || unit === "second" || unit === "hours" || unit === "hour") {
    const duration = positiveDecimal(inputs.duration)
    if (Option.isNone(duration)) return invalid(`Enter a positive duration in ${unit.startsWith("hour") ? "hours" : "seconds"}.`)
    quantity = { numerator: duration.value.numerator * executions.value, denominator: duration.value.denominator }
    quantityUnit = unit.startsWith("hour") ? "hour" : "second"
  } else if (unit === "pixel" || unit === "pixels") {
    const width = positiveInteger(inputs.width)
    const height = positiveInteger(inputs.height)
    const frames = positiveInteger(inputs.frames)
    if (Option.isNone(width) || Option.isNone(height) || Option.isNone(frames)) {
      return invalid("Enter positive width, height, and frame or image count values.")
    }
    quantity = { numerator: width.value * height.value * frames.value * executions.value, denominator: 1n }
    quantityUnit = "pixel"
  } else if (unit === "720p-pixel-seconds") {
    const width = positiveInteger(inputs.width)
    const height = positiveInteger(inputs.height)
    const fps = positiveInteger(inputs.fps)
    const duration = positiveDecimal(inputs.duration)
    if (Option.isNone(width) || Option.isNone(height) || Option.isNone(fps) || Option.isNone(duration)) {
      return invalid("Enter positive width, height, frames per second, and duration values.")
    }
    quantity = {
      numerator: width.value * height.value * fps.value * duration.value.numerator * executions.value,
      denominator: duration.value.denominator
    }
    quantityUnit = "pixel"
  } else return { _tag: "Unsupported", unit: price.quantity_unit }

  const cost = ceilingDivide(
    quantity.numerator * BigInt(price.numerator),
    quantity.denominator * BigInt(price.denominator)
  )
  return { _tag: "Ready", cost, quantity: decimal(quantity), quantityUnit }
}
