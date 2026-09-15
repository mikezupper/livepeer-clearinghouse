import { describe, expect, it } from "vitest"
import { failureMessage, type UiFailureReason } from "./feedback.js"

describe("failure content", () => {
  it.each<[UiFailureReason, string]>([
    ["authentication", "session ended"],
    ["access", "permission"],
    ["conflict", "first page"],
    ["invalid-response", "incompatible response"],
    ["unavailable", "connection"],
    ["rejected", "information you entered"],
    ["unknown", "contact the administrator"]
  ])("makes %s failures actionable", (reason, recovery) => {
    const message = failureMessage("load network offers", reason)
    expect(message).toContain("load network offers")
    expect(message).toContain(recovery)
  })
})
