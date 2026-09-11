import { Schema } from "effect"
import { describe, expect, it } from "vitest"
import { ADMIN_NAVIGATION_GROUPS, ADMIN_ROUTES, AdminRouteIcon, adminRouteHref, adminRouteHrefForUrl, parseAdminPathname, parseAdminUrl, type AdminRouteId } from "./routes.js"

const routeIds: ReadonlyArray<AdminRouteId> = ["overview", "tenants", "accounts", "principals", "financials", "policies", "usage", "operations", "audit"]

describe("admin route metadata", () => {
  it("describes every destination once with a labeled group", () => {
    expect(ADMIN_ROUTES.map(({ id }) => id)).toEqual(routeIds)
    expect(new Set(ADMIN_ROUTES.map(({ id }) => id)).size).toBe(routeIds.length)
    const groups = new Set(ADMIN_NAVIGATION_GROUPS.map(({ id }) => id))
    expect(ADMIN_ROUTES.every(({ label, group, icon }) => label.length > 0 && groups.has(group) && Schema.is(AdminRouteIcon)(icon))).toBe(true)
    expect(new Set(ADMIN_ROUTES.map(({ icon }) => icon)).size).toBe(routeIds.length)
  })

  it("exposes immutable route and group tables", () => {
    expect(Object.isFrozen(ADMIN_ROUTES)).toBe(true)
    expect(Object.isFrozen(ADMIN_NAVIGATION_GROUPS)).toBe(true)
    expect(ADMIN_ROUTES.every(Object.isFrozen)).toBe(true)
    expect(ADMIN_NAVIGATION_GROUPS.every(Object.isFrozen)).toBe(true)
  })
})

describe("admin path parsing", () => {
  it.each(routeIds)("parses the direct %s route with or without a trailing slash", (route) => {
    const path = route === "overview" ? "/" : `/${route}`
    expect(parseAdminPathname(path)).toEqual({ route, mount: "direct" })
    expect(parseAdminPathname(path === "/" ? "///" : `${path}/`)).toEqual({ route, mount: "direct" })
  })

  it.each(routeIds)("parses the admin-prefixed %s route with or without a trailing slash", (route) => {
    const path = route === "overview" ? "/admin" : `/admin/${route}`
    expect(parseAdminPathname(path)).toEqual({ route, mount: "admin" })
    expect(parseAdminPathname(`${path}/`)).toEqual({ route, mount: "admin" })
  })

  it("falls back to overview while retaining the detected mount", () => {
    expect(parseAdminPathname("/unknown")).toEqual({ route: "overview", mount: "direct" })
    expect(parseAdminPathname("/admin/unknown")).toEqual({ route: "overview", mount: "admin" })
    expect(parseAdminPathname("/accounts/nested")).toEqual({ route: "overview", mount: "direct" })
    expect(parseAdminPathname("/admin/accounts/nested")).toEqual({ route: "overview", mount: "admin" })
  })

  it("parses URL pathnames without observing browser state", () => {
    expect(parseAdminUrl(new URL("https://clearinghouse.example/admin/operations/?focus=metering#health"))).toEqual({ route: "operations", mount: "admin" })
    expect(parseAdminUrl(new URL("http://127.0.0.1:4173/usage/?account=acct"))).toEqual({ route: "usage", mount: "direct" })
  })
})

describe("admin href generation", () => {
  it.each(routeIds)("generates direct and admin-prefixed hrefs for %s", (route) => {
    expect(adminRouteHref(route, "direct")).toBe(route === "overview" ? "/" : `/${route}`)
    expect(adminRouteHref(route, "admin")).toBe(route === "overview" ? "/admin" : `/admin/${route}`)
  })

  it("preserves the current deployment mount when generating a href", () => {
    expect(adminRouteHrefForUrl("audit", new URL("http://localhost:5173/tenants"))).toBe("/audit")
    expect(adminRouteHrefForUrl("audit", new URL("https://example.test/admin/accounts"))).toBe("/admin/audit")
  })
})
