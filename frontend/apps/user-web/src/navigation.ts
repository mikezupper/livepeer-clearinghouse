import { Schema } from "effect"

export const UserRoute = Schema.Literal(
  "overview",
  "credentials",
  "sessions",
  "catalog",
  "usage",
  "charges",
  "profile"
)

export type UserRoute = typeof UserRoute.Type

export const UserNavigationIcon = Schema.Literal(
  "wallet",
  "key",
  "linked-clock",
  "catalog-grid",
  "activity-bars",
  "receipt",
  "user-shield"
)

export type UserNavigationIcon = typeof UserNavigationIcon.Type

export const userRoutes: ReadonlyArray<UserRoute> = Object.freeze([
  "overview",
  "credentials",
  "sessions",
  "catalog",
  "usage",
  "charges",
  "profile"
])

export const isUserRoute = Schema.is(UserRoute)

export interface UserNavigationItem {
  readonly route: UserRoute
  readonly label: string
  readonly href: string
  readonly summary: string
  readonly icon: UserNavigationIcon
}

const overviewNavigation: UserNavigationItem = Object.freeze({
  route: "overview",
  label: "Overview",
  href: "/",
  summary: "Monitor your available balance and account exposure.",
  icon: "wallet"
})

export const userNavigation: ReadonlyArray<UserNavigationItem> = Object.freeze([
  overviewNavigation,
  Object.freeze({ route: "credentials", label: "Credentials", href: "/credentials", summary: "Issue and control API access for your account.", icon: "key" }),
  Object.freeze({ route: "sessions", label: "Sessions", href: "/sessions", summary: "Open bounded remote-signer sessions and manage their lifecycle.", icon: "linked-clock" }),
  Object.freeze({ route: "catalog", label: "Catalog", href: "/catalog", summary: "Review enabled capabilities and their exact rates.", icon: "catalog-grid" }),
  Object.freeze({ route: "usage", label: "Usage", href: "/usage", summary: "Inspect the metered activity settled against this account.", icon: "activity-bars" }),
  Object.freeze({ route: "charges", label: "Charges", href: "/charges", summary: "Trace exact charges back to their usage events.", icon: "receipt" }),
  Object.freeze({ route: "profile", label: "Profile", href: "/profile", summary: "Review identity and browser-session security.", icon: "user-shield" })
])

export const userRouteHref = (route: UserRoute): string => userNavigation.find((item) => item.route === route)?.href ?? "/"

export const userRouteMetadata = (route: UserRoute): UserNavigationItem =>
  userNavigation.find((item) => item.route === route) ?? overviewNavigation

export const userRouteFromPathname = (pathname: string): UserRoute => {
  const candidate = pathname.replace(/^\/+|\/+$/gu, "") || "overview"
  return isUserRoute(candidate) ? candidate : "overview"
}
