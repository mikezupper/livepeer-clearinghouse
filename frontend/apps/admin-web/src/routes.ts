import { Schema } from "effect"

export const AdminRoute = Schema.Literal("overview", "users", "workloads", "usage", "operations")
export type AdminRoute = typeof AdminRoute.Type
export const AdminIcon = Schema.Literal("pulse", "users", "workload", "activity", "stop")
export type AdminIcon = typeof AdminIcon.Type
export interface Item { readonly route: AdminRoute; readonly label: string; readonly href: string; readonly summary: string; readonly icon: AdminIcon }
export const routes = Object.freeze([
  { route: "overview", label: "Overview", href: "/admin", summary: "Monitor the clearinghouse core.", icon: "pulse" },
  { route: "users", label: "Users & accounts", href: "/admin/users", summary: "Inspect direct user-to-account access.", icon: "users" },
  { route: "workloads", label: "Workloads", href: "/admin/workloads", summary: "Inspect quoted jobs and signer state.", icon: "workload" },
  { route: "usage", label: "Usage", href: "/admin/usage", summary: "Monitor attribution and signer-reported fees.", icon: "activity" },
  { route: "operations", label: "Operations", href: "/admin/operations", summary: "Control the global signer authorization stop.", icon: "stop" }
] satisfies ReadonlyArray<Item>)
const isRoute = Schema.is(AdminRoute)
export const routeFromPath = (pathname: string): AdminRoute => {
  const value = pathname.replace(/^\/admin\/?/u, "").replace(/\/+$/u, "") || "overview"
  return isRoute(value) ? value : "overview"
}
export const metadata = (route: AdminRoute): Item => routes.find((item) => item.route === route) ?? {
  route: "overview", label: "Overview", href: "/admin", summary: "Monitor the clearinghouse core.", icon: "pulse"
}
