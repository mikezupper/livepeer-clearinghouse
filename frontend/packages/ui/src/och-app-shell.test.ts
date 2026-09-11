import { describe, expect, it } from "vitest"
import "./och-app-shell.js"
import type { OchAppShell } from "./och-app-shell.js"

const mount = (): OchAppShell => {
  const element = document.createElement("och-app-shell")
  document.body.append(element)
  return element
}

describe("och-app-shell", () => {
  it("renders a complete semantic document region", async () => {
    const element = mount()
    await element.updateComplete
    const root = element.shadowRoot
    expect(root?.querySelectorAll("main")).toHaveLength(1)
    expect(root?.querySelectorAll("h1")).toHaveLength(1)
    expect(root?.querySelector("nav")?.getAttribute("aria-label")).toBe("Primary")
    expect(root?.querySelector("aside")?.getAttribute("aria-label")).toBe("Primary")
    expect(root?.querySelector("a")?.getAttribute("href")).toBe("#main")
    expect(root?.querySelector("footer small")?.textContent).toContain("Livepeer")
    expect(root?.querySelector("[part~='brand-name']")?.textContent).toContain("Livepeer Clearinghouse")
    expect(root?.querySelector("[part~='brand-subtitle']")?.textContent).toBe("Identity & Payments")
    expect(root?.querySelector("[part~='mobile-brand']")?.textContent).toContain("Livepeer Clearinghouse")
    expect(root?.querySelector("slot[name='context']")).not.toBeNull()
    expect(root?.querySelector("slot[name='utility']")).not.toBeNull()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth)
  })

  it("reacts to public content properties", async () => {
    const element = mount()
    element.heading = "Administration"
    element.summary = "Operate the clearinghouse."
    await element.updateComplete
    expect(element.shadowRoot?.querySelector("h1")?.textContent).toBe("Administration")
    expect(element.shadowRoot?.querySelector("hgroup p")?.textContent).toBe("Operate the clearinghouse.")
    element.navigationLabel = ""
    await element.updateComplete
    expect(element.shadowRoot?.querySelector("nav")?.getAttribute("aria-label")).toBe("Primary")
  })

  it("exposes every global customization surface", async () => {
    const element = mount()
    await element.updateComplete
    const parts = Array.from(element.shadowRoot?.querySelectorAll("[part]") ?? [])
      .flatMap((node) => node.getAttribute("part")?.split(" ") ?? [])
    expect(parts).toEqual(expect.arrayContaining([
      "skip-link", "shell", "sidebar", "header", "sidebar-header", "brand", "brand-name", "brand-accent",
      "brand-subtitle", "sidebar-close-button", "sidebar-close-icon", "sidebar-close-label", "navigation",
      "navigation-backdrop", "workspace", "topbar", "menu-button", "menu-icon", "menu-label", "mobile-brand",
      "context", "utility", "main", "heading-group", "title", "summary",
      "content", "footer", "footer-note"
    ]))
  })

  it("exposes an accessible mobile navigation control", async () => {
    const element = mount()
    await element.updateComplete
    const button = element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='menu-button']")
    expect(button?.getAttribute("aria-controls")).toBe("primary-navigation")
    expect(button?.getAttribute("aria-expanded")).toBe("false")
    expect(button?.getAttribute("aria-label")).toBe("Open navigation")
    expect(button?.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true")

    button?.click()
    await element.updateComplete
    expect(element.hasAttribute("navigation-open")).toBe(true)
    expect(element.shadowRoot?.querySelector("[part~='workspace']")?.hasAttribute("inert")).toBe(true)
    expect(button?.getAttribute("aria-expanded")).toBe("true")
    expect(button?.getAttribute("aria-label")).toBe("Close navigation")
    expect(button?.textContent?.trim()).toBe("Close navigation")
    expect(element.shadowRoot?.activeElement?.getAttribute("part")).toContain("sidebar-close-button")

    button?.click()
    await element.updateComplete
    expect(element.hasAttribute("navigation-open")).toBe(false)
    expect(element.shadowRoot?.querySelector("[part~='workspace']")?.hasAttribute("inert")).toBe(false)
  })

  it("closes mobile navigation with Escape and restores control focus", async () => {
    const element = mount()
    await element.updateComplete
    const shell = element.shadowRoot?.querySelector("[part~='shell']")
    shell?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    expect(element.hasAttribute("navigation-open")).toBe(false)
    const button = element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='menu-button']")
    button?.click()
    await element.updateComplete
    shell?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }))
    expect(element.hasAttribute("navigation-open")).toBe(true)
    shell?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    await element.updateComplete
    expect(element.hasAttribute("navigation-open")).toBe(false)
    expect(element.shadowRoot?.activeElement).toBe(button)
  })

  it("closes mobile navigation after a slotted navigation link is activated", async () => {
    const element = document.createElement("och-app-shell") as OchAppShell
    const link = document.createElement("a")
    link.slot = "navigation"
    link.href = "#destination"
    link.textContent = "Destination"
    element.append(link)
    document.body.append(element)
    await element.updateComplete
    element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='menu-button']")?.click()
    await element.updateComplete

    link.click()
    await element.updateComplete
    expect(element.hasAttribute("navigation-open")).toBe(false)
  })

  it("closes mobile navigation from the labelled backdrop", async () => {
    const element = mount()
    await element.updateComplete
    element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='menu-button']")?.click()
    await element.updateComplete
    const backdrop = element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='navigation-backdrop']")
    expect(backdrop?.getAttribute("aria-label")).toBe("Close navigation")
    backdrop?.click()
    await element.updateComplete
    expect(element.hasAttribute("navigation-open")).toBe(false)
  })

  it("closes modal mobile navigation from the sidebar control", async () => {
    const element = mount()
    await element.updateComplete
    element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='menu-button']")?.click()
    await element.updateComplete
    const close = element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='sidebar-close-button']")
    expect(close?.getAttribute("aria-label")).toBe("Close navigation")
    close?.click()
    await element.updateComplete
    expect(element.hasAttribute("navigation-open")).toBe(false)
    expect(element.shadowRoot?.querySelector("[part~='workspace']")?.hasAttribute("inert")).toBe(false)
  })
})
