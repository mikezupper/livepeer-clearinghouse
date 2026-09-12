import { Schema } from "effect"

export const UserRoute = Schema.Literal("overview", "discovery", "estimate", "workloads", "credentials", "usage", "profile")
export type UserRoute = typeof UserRoute.Type
export const UserIcon = Schema.Literal("pulse", "catalog", "calculator", "workload", "key", "activity", "user")
export type UserIcon = typeof UserIcon.Type
export interface NavigationItem {
  readonly route: UserRoute; readonly label: string; readonly href: string
  readonly summary: string; readonly icon: UserIcon
}
export const navigation = Object.freeze([
  { route: "overview", label: "Overview", href: "/", summary: "Start and inspect Livepeer workloads.", icon: "pulse" },
  { route: "discovery", label: "Network", href: "/discovery", summary: "Compare capabilities, runners, and advertised prices.", icon: "catalog" },
  { route: "estimate", label: "Cost estimator", href: "/estimate", summary: "Estimate a workload at a currently advertised exact rate.", icon: "calculator" },
  { route: "workloads", label: "Workloads", href: "/workloads", summary: "Create quoted SDK access and control active work.", icon: "workload" },
  { route: "credentials", label: "API credentials", href: "/credentials", summary: "Issue and revoke Python SDK credentials.", icon: "key" },
  { route: "usage", label: "Usage & cost", href: "/usage", summary: "Compare measured quantity, quoted cost, and signer fees.", icon: "activity" },
  { route: "profile", label: "Profile", href: "/profile", summary: "Review account identity and session security.", icon: "user" }
] satisfies ReadonlyArray<NavigationItem>)
const isRoute = Schema.is(UserRoute)
export const routeFromPath = (pathname: string): UserRoute => {
  const value = pathname.replace(/^\/+|\/+$/gu, "") || "overview"
  return isRoute(value) ? value : "overview"
}
export const routeMetadata = (route: UserRoute): NavigationItem =>
  navigation.find((item) => item.route === route) ?? {
    route: "overview", label: "Overview", href: "/", summary: "Start and inspect Livepeer workloads.", icon: "pulse"
  }
