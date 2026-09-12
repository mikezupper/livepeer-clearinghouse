import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  denominationStorageKey,
  formatAmount,
  parseDisplayDenomination,
  readDisplayDenomination,
  setDisplayDenomination,
  subscribeDisplayDenomination
} from "./denomination.js"
import "./och-denomination-control.js"
import type { OchDenominationControl } from "./och-denomination-control.js"

describe("display denomination", () => {
  beforeEach(() => {
    document.body.replaceChildren()
    localStorage.removeItem(denominationStorageKey)
  })
  afterEach(() => localStorage.removeItem(denominationStorageKey))

  it("defaults unknown and absent stored values to wei", () => {
    expect(parseDisplayDenomination(null)).toBe("wei")
    expect(parseDisplayDenomination("gwei")).toBe("wei")
    expect(parseDisplayDenomination("eth")).toBe("eth")
    expect(readDisplayDenomination({ getItem: () => "invalid" })).toBe("wei")
  })

  it("formats integral wei as exact decimal ETH without floating point", () => {
    expect(formatAmount("1000000000000000000", "1", "wei", "eth")).toEqual({
      primary: "1 ETH",
      exactWei: "1000000000000000000 wei"
    })
    expect(formatAmount(20n, 1n, "WEI", "eth")).toEqual({
      primary: "0.00000000000000002 ETH",
      exactWei: "20 wei"
    })
  })

  it("marks repeating ETH rates approximate and retains the exact wei rational", () => {
    expect(formatAmount("1", "3", "wei", "eth")).toEqual({
      primary: "≈0.000000000000000000333333 ETH",
      exactWei: "1 / 3 wei"
    })
    expect(formatAmount("5", "2", "wei", "eth")).toEqual({
      primary: "0.0000000000000000025 ETH",
      exactWei: "5 / 2 wei"
    })
  })

  it("leaves native non-wei currencies and wei display unchanged", () => {
    expect(formatAmount("7", "2", "USD", "eth")).toEqual({
      primary: "7 / 2 USD",
      exactWei: undefined
    })
    expect(formatAmount("7", "1", "wei", "wei")).toEqual({
      primary: "7 wei",
      exactWei: undefined
    })
  })

  it("persists changes and synchronizes same-document subscribers", () => {
    const listener = vi.fn()
    const unsubscribe = subscribeDisplayDenomination(listener)
    setDisplayDenomination("eth")
    expect(localStorage.getItem(denominationStorageKey)).toBe("eth")
    expect(listener).toHaveBeenLastCalledWith("eth")
    unsubscribe()
    setDisplayDenomination("wei")
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it("synchronizes storage changes from another same-origin document", () => {
    const listener = vi.fn()
    const unsubscribe = subscribeDisplayDenomination(listener)
    window.dispatchEvent(new StorageEvent("storage", {
      key: denominationStorageKey,
      newValue: "eth"
    }))
    window.dispatchEvent(new StorageEvent("storage", { key: "unrelated", newValue: "wei" }))
    expect(listener).toHaveBeenCalledOnce()
    expect(listener).toHaveBeenCalledWith("eth")
    unsubscribe()
  })

  it("renders a labelled native selector and reacts to local and cross-tab changes", async () => {
    const element = document.createElement("och-denomination-control") as OchDenominationControl
    document.body.append(element)
    await element.updateComplete
    const select = element.shadowRoot?.querySelector<HTMLSelectElement>("select")
    expect(element.shadowRoot?.querySelector("label")?.textContent).toContain("Display")
    expect(select?.getAttribute("aria-label")).toBe("Currency denomination")
    expect(select?.value).toBe("wei")

    if (select) {
      select.value = "eth"
      select.dispatchEvent(new Event("change", { bubbles: true }))
    }
    await element.updateComplete
    expect(localStorage.getItem(denominationStorageKey)).toBe("eth")
    expect(select?.value).toBe("eth")

    window.dispatchEvent(new StorageEvent("storage", {
      key: denominationStorageKey,
      newValue: "wei"
    }))
    await element.updateComplete
    expect(select?.value).toBe("wei")
  })
})
