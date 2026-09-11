import { Schema } from "effect"

export const AdminRouteId = Schema.Literal("overview", "tenants", "accounts", "principals", "financials", "policies", "usage", "operations", "audit")
export type AdminRouteId = typeof AdminRouteId.Type

export const AdminNavigationGroupId = Schema.Literal("clearinghouse", "access", "economics", "metering", "governance")
export type AdminNavigationGroupId = typeof AdminNavigationGroupId.Type

export const AdminRouteMount = Schema.Literal("direct", "admin")
export type AdminRouteMount = typeof AdminRouteMount.Type

export const AdminRouteIcon = Schema.Literal("pulse", "building", "wallet", "principals", "ledger", "policy", "usage", "operations", "audit")
export type AdminRouteIcon = typeof AdminRouteIcon.Type

export interface AdminNavigationGroup {
  readonly id: AdminNavigationGroupId
  readonly label: string
}

export interface AdminRoute {
  readonly id: AdminRouteId
  readonly label: string
  readonly group: AdminNavigationGroupId
  readonly icon: AdminRouteIcon
}

export interface AdminLocation {
  readonly route: AdminRouteId
  readonly mount: AdminRouteMount
}

export const ADMIN_NAVIGATION_GROUPS: ReadonlyArray<AdminNavigationGroup> = Object.freeze([Object.freeze({ id: "clearinghouse", label: "Clearinghouse" }), Object.freeze({ id: "access", label: "Access" }), Object.freeze({ id: "economics", label: "Economics" }), Object.freeze({ id: "metering", label: "Metering" }), Object.freeze({ id: "governance", label: "Governance" })])

export const ADMIN_ROUTES: ReadonlyArray<AdminRoute> = Object.freeze([Object.freeze({ id: "overview", label: "Overview", group: "clearinghouse", icon: "pulse" }), Object.freeze({ id: "tenants", label: "Tenants", group: "access", icon: "building" }), Object.freeze({ id: "accounts", label: "Payer accounts", group: "access", icon: "wallet" }), Object.freeze({ id: "principals", label: "Principals", group: "access", icon: "principals" }), Object.freeze({ id: "financials", label: "Financials", group: "economics", icon: "ledger" }), Object.freeze({ id: "policies", label: "Policies & pricing", group: "economics", icon: "policy" }), Object.freeze({ id: "usage", label: "Usage & charges", group: "metering", icon: "usage" }), Object.freeze({ id: "operations", label: "Operations", group: "metering", icon: "operations" }), Object.freeze({ id: "audit", label: "Audit trail", group: "governance", icon: "audit" })])

const isAdminRouteId = Schema.is(AdminRouteId)

const pathSegments = (pathname: string): ReadonlyArray<string> => pathname.split("/").filter((segment) => segment.length > 0)

export const parseAdminPathname = (pathname: string): AdminLocation => {
  const segments = pathSegments(pathname)
  const mount: AdminRouteMount = segments[0] === "admin" ? "admin" : "direct"
  const routeSegment = mount === "admin" ? segments[1] : segments[0]
  const expectedSegments = mount === "admin" ? 2 : 1
  const isRoot = segments.length === 0 || (mount === "admin" && segments.length === 1)

  return Object.freeze({ mount, route: isRoot || segments.length !== expectedSegments || !isAdminRouteId(routeSegment) ? "overview" : routeSegment })
}

export const parseAdminUrl = (url: URL): AdminLocation => parseAdminPathname(url.pathname)

export const adminRouteHref = (route: AdminRouteId, mount: AdminRouteMount): string => {
  const prefix = mount === "admin" ? "/admin" : ""
  return route === "overview" ? prefix || "/" : `${prefix}/${route}`
}

export const adminRouteHrefForUrl = (route: AdminRouteId, currentUrl: URL): string => adminRouteHref(route, parseAdminUrl(currentUrl).mount)
