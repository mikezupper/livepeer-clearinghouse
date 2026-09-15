/* eslint-disable @typescript-eslint/no-non-null-assertion -- real-browser fixture assertions */
import { afterEach, describe, expect, it } from "vitest"
import "./och-help-tip.js"
import type { OchHelpTip } from "./och-help-tip.js"

describe("contextual help", () => {
  afterEach(() => document.body.replaceChildren())

  it("exposes a named native popover without hiding slotted meaning", async () => {
    const element = document.createElement("och-help-tip") as OchHelpTip
    element.term = "signer-reported cost"
    element.textContent = "The fee reported by signer metering."
    document.body.append(element)
    await element.updateComplete

    const button = element.shadowRoot!.querySelector("button")!
    const popover = element.shadowRoot!.querySelector<HTMLElement>("[popover]")!
    expect(button.getAttribute("aria-label")).toBe("Help: signer-reported cost")
    expect(button.getAttribute("popovertarget")).toBe("context-help")
    expect(popover.getAttribute("role")).toBe("note")
    expect(element.textContent).toContain("fee reported by signer metering")
  })

  it("opens and dismisses through native button interaction", async () => {
    const element = document.createElement("och-help-tip") as OchHelpTip
    document.body.append(element)
    await element.updateComplete
    const button = element.shadowRoot!.querySelector("button")!
    const popover = element.shadowRoot!.querySelector<HTMLElement>("[popover]")!

    button.click()
    expect(popover.matches(":popover-open")).toBe(true)
    button.click()
    expect(popover.matches(":popover-open")).toBe(false)
  })
})
