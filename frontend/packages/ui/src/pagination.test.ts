import { LitElement } from "lit"
import { afterEach, describe, expect, it } from "vitest"
import { CursorPaginationController } from "./pagination.js"

class Host extends LitElement {
  readonly pages = new CursorPaginationController(this)
}
customElements.define("pagination-test-host", Host)

describe("cursor pagination controller", () => {
  afterEach(() => {
    document.body.replaceChildren()
    history.replaceState(null, "", "/")
  })

  it("keeps opaque current state in the URL and walks its back-stack", () => {
    const host = new Host()
    document.body.append(host)
    host.pages.received("next-token")
    expect(host.pages.move("next")).toBe(true)
    expect(new URL(location.href).searchParams.get("cursor")).toBe("next-token")
    expect(host.pages.pageNumber).toBe(2)
    host.pages.restore()
    expect(host.pages.pageNumber).toBe(2)
    host.pages.received(null)
    expect(host.pages.move("previous")).toBe(true)
    expect(new URL(location.href).searchParams.has("cursor")).toBe(false)
  })

  it("does not move without a cursor and resets all state", () => {
    const host = new Host()
    document.body.append(host)
    expect(host.pages.move("next")).toBe(false)
    host.pages.received("next-token")
    host.pages.move("next")
    host.pages.reset()
    expect(host.pages.pageNumber).toBe(1)
    expect(host.pages.hasPrevious).toBe(false)
  })
})
