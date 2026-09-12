import { describe, expect, it } from "vitest"
import { estimateQuote, type QuoteInputs } from "./quote-estimator.js"

const inputs: QuoteInputs = {
  executions: "2", duration: "1.5", width: "1280", height: "720", frames: "3", fps: "30"
}
const price = (quantity_unit: string, numerator = "5", denominator = "2") => ({
  numerator, denominator, currency: "wei", quantity_unit
})

describe("quote estimator", () => {
  it.each([
    ["fixed", "2", "fixed execution", 5n],
    ["seconds", "3", "second", 8n],
    ["pixel", "5529600", "pixel", 13824000n],
    ["720p-pixel-seconds", "82944000", "pixel", 207360000n]
  ] as const)("estimates %s units using ceiling arithmetic", (unit, quantity, quantityUnit, cost) => {
    expect(estimateQuote(price(unit), inputs)).toEqual({ _tag: "Ready", quantity, quantityUnit, cost })
  })

  it("keeps large prices exact beyond JavaScript's safe integer range", () => {
    expect(estimateQuote(price("fixed", "9007199254740993", "1"), inputs)).toMatchObject({
      _tag: "Ready", cost: 18014398509481986n
    })
  })

  it("preserves fractional second quantities and supports hourly rates", () => {
    expect(estimateQuote(price("seconds"), { ...inputs, executions: "1" })).toEqual({
      _tag: "Ready", quantity: "1.5", quantityUnit: "second", cost: 4n
    })
    expect(estimateQuote(price("hours"), { ...inputs, executions: "1" })).toMatchObject({
      _tag: "Ready", quantityUnit: "hour", cost: 4n
    })
  })

  it.each([
    [{ ...inputs, executions: "0" }, "Enter at least one execution."],
    [{ ...inputs, duration: "0" }, "Enter a positive duration in seconds."],
    [{ ...inputs, duration: "unknown" }, "Enter a positive duration in seconds."],
    [{ ...inputs, width: "-1" }, "Enter positive width, height, and frame or image count values."],
    [{ ...inputs, fps: "0" }, "Enter positive width, height, frames per second, and duration values."]
  ] as const)("rejects invalid assumptions", (values, message) => {
    expect(estimateQuote(price(message.includes("frames per second") ? "720p-pixel-seconds" : message.includes("width") ? "pixel" : "seconds"), values)).toEqual({ _tag: "Invalid", message })
  })

  it("fails safely for billing units without an estimator", () => {
    expect(estimateQuote(price("requests-per-fortnight"), inputs)).toEqual({
      _tag: "Unsupported", unit: "requests-per-fortnight"
    })
  })
})
