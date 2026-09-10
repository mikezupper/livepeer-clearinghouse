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
    expect(root?.querySelector("a")?.getAttribute("href")).toBe("#main")
    expect(root?.querySelector("footer small")?.textContent).toContain("Livepeer")
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
      "skip-link", "header", "brand", "navigation", "main", "heading-group",
      "title", "summary", "footer", "footer-note"
    ]))
  })
})
