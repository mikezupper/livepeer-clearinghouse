/* eslint-disable @typescript-eslint/no-non-null-assertion -- real-browser fixture assertions */
import { afterEach, describe, expect, it, vi } from "vitest"
import "./och-cursor-pagination.js"
import type { OchCursorPagination } from "./och-cursor-pagination.js"

describe("cursor pagination control", () => {
  afterEach(() => document.body.replaceChildren())

  it("exposes semantic navigation and emits bounded directions", async () => {
    const element = document.createElement("och-cursor-pagination") as OchCursorPagination
    element.hasPrevious = true
    element.hasNext = true
    element.page = 3
    const listener = vi.fn()
    element.addEventListener("cursor-page-change", listener)
    document.body.append(element)
    await element.updateComplete

    const navigation = element.shadowRoot!.querySelector("nav")!
    expect(navigation.getAttribute("aria-label")).toBe("Collection pages")
    expect(navigation.textContent).toContain("Page 3")
    const buttons = element.shadowRoot!.querySelectorAll("button")
    buttons[0]!.click()
    buttons[1]!.click()
    expect(listener.mock.calls.map(([event]) => event.detail.direction)).toEqual([
      "previous", "next"
    ])
  })

  it("disables unavailable and busy navigation", async () => {
    const element = document.createElement("och-cursor-pagination") as OchCursorPagination
    document.body.append(element)
    await element.updateComplete
    expect([...element.shadowRoot!.querySelectorAll("button")].every((value) => value.disabled)).toBe(true)
    element.hasNext = true
    element.busy = true
    await element.updateComplete
    expect([...element.shadowRoot!.querySelectorAll("button")].every((value) => value.disabled)).toBe(true)
  })
})
