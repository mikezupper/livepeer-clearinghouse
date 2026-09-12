import { describe, expect, it } from "vitest"
import { metadata, routeFromPath, routes } from "./routes.js"

describe("admin routes", () => {
  it("parses mounted paths and rejects removed enterprise routes", () => {
    expect(routeFromPath("/admin/usage/")).toBe("usage")
    expect(routeFromPath("/admin/financials")).toBe("overview")
    expect(metadata("operations").label).toBe("Operations")
    expect(metadata("invalid" as never).route).toBe("overview")
    expect(routes).toHaveLength(5)
  })
})
