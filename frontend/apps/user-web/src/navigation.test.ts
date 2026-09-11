import { describe, expect, it } from "vitest"
import { Schema } from "effect"
import { UserNavigationIcon, UserRoute, userNavigation, userRouteFromPathname, userRouteHref, userRouteMetadata, userRoutes } from "./navigation.js"

describe("user console navigation", () => {
  it("preserves every route, label, and URL in navigation order", () => {
    expect(userNavigation).toEqual([
      { route: "overview", label: "Overview", href: "/", summary: "Monitor your available balance and account exposure.", icon: "wallet" },
      { route: "credentials", label: "Credentials", href: "/credentials", summary: "Issue and control API access for your account.", icon: "key" },
      { route: "sessions", label: "Sessions", href: "/sessions", summary: "Open bounded remote-signer sessions and manage their lifecycle.", icon: "linked-clock" },
      { route: "catalog", label: "Catalog", href: "/catalog", summary: "Review enabled capabilities and their exact rates.", icon: "catalog-grid" },
      { route: "usage", label: "Usage", href: "/usage", summary: "Inspect the metered activity settled against this account.", icon: "activity-bars" },
      { route: "charges", label: "Charges", href: "/charges", summary: "Trace exact charges back to their usage events.", icon: "receipt" },
      { route: "profile", label: "Profile", href: "/profile", summary: "Review identity and browser-session security.", icon: "user-shield" }
    ])
  })

  it("generates the canonical href for every route", () => {
    for (const item of userNavigation) {
      expect(userRouteHref(item.route)).toBe(item.href)
      expect(userRouteMetadata(item.route)).toBe(item)
    }
  })

  it("parses every canonical route and its trailing-slash form", () => {
    for (const item of userNavigation) {
      expect(userRouteFromPathname(item.href)).toBe(item.route)
      expect(userRouteFromPathname(`${item.href}/`)).toBe(item.route)
    }
  })

  it("uses overview for empty, root, and unknown paths", () => {
    for (const pathname of ["", "/", "///", "/unknown", "/usage/details", "/unknown/"]) {
      expect(userRouteFromPathname(pathname)).toBe("overview")
    }
  })

  it("exposes the same route order as the navigation metadata", () => {
    expect(userNavigation.map(({ route }) => route)).toEqual(userRoutes)
  })

  it("models every route and icon identifier with Effect Schema", () => {
    const isRoute = Schema.is(UserRoute)
    const isIcon = Schema.is(UserNavigationIcon)
    expect(userNavigation.every(({ route, icon }) => isRoute(route) && isIcon(icon))).toBe(true)
    expect(new Set(userNavigation.map(({ icon }) => icon)).size).toBe(userNavigation.length)
    expect(isRoute("billing")).toBe(false)
    expect(isIcon("chart-library-icon")).toBe(false)
  })
})
