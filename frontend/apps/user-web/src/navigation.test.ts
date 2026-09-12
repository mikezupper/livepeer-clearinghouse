import { describe, expect, it } from "vitest"
import { navigation, routeFromPath, routeMetadata } from "./navigation.js"

describe("user navigation", () => {
  it("parses core routes and falls back safely", () => {
    expect(routeFromPath("/usage/")).toBe("usage")
    expect(routeFromPath("/estimate/")).toBe("estimate")
    expect(routeFromPath("/enterprise-ledger")).toBe("overview")
    expect(routeMetadata("workloads").label).toBe("Workloads")
    expect(navigation).toHaveLength(7)
  })
})
