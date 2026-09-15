/* eslint-disable @typescript-eslint/no-non-null-assertion -- test fixtures assert rendered DOM */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import "./admin-app.js"
import type { AdminApp } from "./admin-app.js"

const at = "2026-09-11T14:00:00+00:00"
const session = { user_id: "usr_admin", account_id: "acct_admin", email: "admin@example.com", is_admin: true, expires_at: at }
const workload = { id: "work_123", account_id: "acct_user", capability: "live", model: "noop", offer_id: "price_123", quoted_price: { numerator: "2", denominator: "1", currency: "wei", quantity_unit: "pixel" }, status: "active", client_reference: "job-1", runner_session_id: null, manifest_id: null, payment_session_id: null, max_spend_wei: "100", created_at: at, expires_at: at }
const users = [{ user_id: "usr_user", account_id: "acct_user", email: "user@example.com", is_admin: false }]
const listedWorkloads = [workload, { ...workload, id: "work_expired", status: "expired" }, { ...workload, id: "work_ended", status: "ended" }]
const overview = { users: 1, workloads: 3, active_workloads: 1, usage: 3, unmatched_usage: 1, computed_fee: "42", currency: "wei", global_stop: { enabled: false, reason: "Normal operation", changed_at: at } }
const reply = (body?: unknown, status = 200): Promise<Response> => Promise.resolve(new Response(body === undefined ? null : JSON.stringify(body), { status, headers: body === undefined ? {} : { "Content-Type": "application/json" } }))

describe("admin application", () => {
  let signedIn = false
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    history.replaceState(null, "", "/admin")
    document.body.replaceChildren()
    localStorage.removeItem("och.display-denomination")
    document.cookie = "och_csrf=test-csrf; path=/"
    fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email", "github"] })
      if (path === "/v1/auth/session" && init?.method === "DELETE") { signedIn = false; return reply(undefined, 204) }
      if (path === "/v1/auth/session") return signedIn ? reply(session) : reply({}, 401)
      if (path === "/v1/auth/email/code") return reply(undefined, 202)
      if (path === "/v1/auth/email/verify") { signedIn = true; return reply(session) }
      if (path === "/v1/admin/overview") return reply(overview)
      if (path === "/v1/admin/users") return reply({ items: users, next_cursor: null })
      if (path === "/v1/admin/workloads") return reply({ items: listedWorkloads, next_cursor: null })
      if (path === "/v1/admin/global-stop") {
        const enabled = JSON.parse(String(init?.body)).enabled as boolean
        return reply({ enabled, reason: "maintenance", changed_at: at })
      }
      return reply({}, 404)
    })
    vi.stubGlobal("fetch", fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it("signs in, navigates core operations, and controls global authorization", async () => {
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Sign in to administer"))
    const request = element.shadowRoot!.querySelector("form")!
    request.querySelector<HTMLInputElement>("input")!.value = "admin@example.com"
    request.requestSubmit()
    request.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Administrator sign-in code sent"))
    expect(fetchMock.mock.calls.filter(([input]) => new URL(String(input), location.origin).pathname === "/v1/auth/email/code")).toHaveLength(1)
    expect(element.shadowRoot?.textContent).toContain("Sign in with GitHub")
    expect(request.querySelector("#email")?.getAttribute("aria-describedby")).toBe("email-hint")
    const verify = element.shadowRoot!.querySelectorAll("form")[1]!
    const fields = verify.querySelectorAll<HTMLInputElement>("input")
    expect(fields[0]?.getAttribute("aria-describedby")).toBe("verify-email-hint")
    expect(fields[1]?.getAttribute("aria-describedby")).toBe("code-hint")
    fields[0]!.value = "admin@example.com"
    fields[1]!.value = "123456"
    verify.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Clearinghouse overview"))
    expect(element.shadowRoot?.textContent).toContain("Review current access, workload, and metering totals")
    expect(element.shadowRoot?.textContent).toContain("Current clearinghouse activity")
    expect(element.shadowRoot?.textContent).toContain("Authorization available")
    expect(element.shadowRoot?.textContent).toContain("42 wei")
    const denomination = element.shadowRoot!.querySelector("och-denomination-control")!.shadowRoot!.querySelector<HTMLSelectElement>("select")!
    denomination.value = "eth"
    denomination.dispatchEvent(new Event("change", { bubbles: true }))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("0.000000000000000042 ETH"))
    expect(element.shadowRoot?.textContent).toContain("42 wei exact")

    const navigate = async (name: string, heading: string): Promise<void> => {
      element.shadowRoot!.querySelector<HTMLAnchorElement>(`a[href='/admin/${name}']`)!.click()
      await vi.waitFor(() => expect(element.shadowRoot?.querySelector("h2")?.textContent).toBe(heading))
    }
    await navigate("users", "Users and accounts")
    expect(element.shadowRoot?.textContent).toContain("personal account that owns their credentials")
    expect(element.shadowRoot?.textContent).toContain("Authenticated access directory")
    expect(element.shadowRoot?.textContent).toContain("user@example.com")
    await navigate("workloads", "Workloads")
    expect(element.shadowRoot?.textContent).toContain("time-bounded authorizations created from network offers")
    expect(element.shadowRoot?.textContent).toContain("status describes signer access")
    expect(element.shadowRoot?.textContent).toContain("work_123")
    expect(element.shadowRoot?.textContent).toContain("Expired")
    expect(element.shadowRoot?.textContent).toContain("Access expires")
    await navigate("usage", "Usage attribution")
    expect(element.shadowRoot?.textContent).toContain("before relying on account-level cost records")
    expect(element.shadowRoot?.textContent).toContain("Unmatched usage events are retained for investigation")
    expect(element.shadowRoot?.querySelector("och-help-tip[term='unmatched usage events']")).not.toBeNull()
    await navigate("operations", "Operations")
    expect(element.shadowRoot?.textContent).toContain("Pausing fails new authorization closed")
    const stop = element.shadowRoot!.querySelector("form")!
    expect(stop.querySelector("#reason")?.getAttribute("aria-describedby")).toBe("reason-hint")
    expect(stop.querySelector("button")?.textContent).toContain("Pause signer authorization")
    stop.querySelector<HTMLInputElement>("#reason")!.value = "maintenance"
    stop.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Signer authorization paused"))
    const resume = element.shadowRoot!.querySelector("form")!
    expect(resume.querySelector("button")?.textContent).toContain("Resume signer authorization")
    resume.querySelector<HTMLInputElement>("#reason")!.value = "maintenance complete"
    resume.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("authorization resumed"))
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='logout']")!.click()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Sign in to administer"))
    expect(fetchMock).toHaveBeenCalled()
  })

  it("shows a clear denial for a signed-in non-admin", async () => {
    signedIn = true
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (path === "/v1/auth/session") return reply({ ...session, is_admin: false })
      return reply({}, 404)
    })
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Administrator access required"))
    expect(element.shadowRoot?.textContent).toContain("Sign out, then sign in with the configured administrator email address")
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='logout']")!.click()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Administrator session ended"))
    expect(element.shadowRoot?.textContent).toContain("Sign in to administer")
  })

  it("distinguishes empty administrator collections from unloaded data", async () => {
    signedIn = true
    let resolveUsers: (response: Response) => void = () => undefined
    const pendingUsers = new Promise<Response>((resolve) => { resolveUsers = resolve })
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (path === "/v1/auth/session") return reply(session)
      if (path === "/v1/admin/overview") return reply({ ...overview, users: 0, workloads: 0, active_workloads: 0 })
      if (path === "/v1/admin/users") return pendingUsers
      if (path === "/v1/admin/workloads") return reply({ items: [], next_cursor: null })
      return reply({}, 404)
    })
    history.replaceState(null, "", "/admin/users")
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Loading users and personal accounts"))
    expect(element.shadowRoot?.textContent).not.toContain("No users or personal accounts have been recorded")
    resolveUsers(new Response(JSON.stringify({ items: [], next_cursor: null }), {
      status: 200, headers: { "Content-Type": "application/json" }
    }))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("No users or personal accounts have been recorded"))
    expect(element.shadowRoot!.querySelector("table")).toBeNull()
    element.shadowRoot!.querySelector<HTMLAnchorElement>("a[href='/admin/workloads']")!.click()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("No workloads recorded"))
    expect(element.shadowRoot?.textContent).toContain("after a user creates quoted access from a network offer")
  })

  it("walks administrator collection cursors and restores the first page", async () => {
    signedIn = true
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = new URL(String(input), location.origin)
      if (url.pathname === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (url.pathname === "/v1/auth/session") return reply(session)
      if (url.pathname === "/v1/admin/overview") return reply(overview)
      if (url.pathname === "/v1/admin/users") return reply({
        items: users, next_cursor: url.searchParams.has("cursor") ? null : "admin-next"
      })
      return reply({}, 404)
    })
    history.replaceState(null, "", "/admin/users")
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("user@example.com"))
    const pagination = element.shadowRoot!.querySelector("och-cursor-pagination")!
    await pagination.updateComplete
    pagination.shadowRoot!.querySelectorAll("button")[1]!.click()
    await vi.waitFor(() => expect(location.search).toContain("cursor=admin-next"))
    await pagination.updateComplete
    pagination.shadowRoot!.querySelectorAll("button")[0]!.click()
    await vi.waitFor(() => expect(location.search).toBe(""))
  })

  it("keeps a large user fixture to one bounded DOM page", async () => {
    signedIn = true
    const page = Array.from({ length: 50 }, (_, index) => ({
      user_id: `usr_${index}`, account_id: `acct_${index}`,
      email: `user-${index}@example.com`, is_admin: false
    }))
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (path === "/v1/auth/session") return reply(session)
      if (path === "/v1/admin/overview") return reply(overview)
      if (path === "/v1/admin/users") return reply({ items: page, next_cursor: "more" })
      return reply({}, 404)
    })
    history.replaceState(null, "", "/admin/users")
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot!.querySelectorAll("tbody tr")).toHaveLength(50))
  })

  it("offers recovery when bootstrap transport fails", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error("offline")))
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("service did not respond"))
    expect(element.shadowRoot?.textContent).toContain("Check your connection and try again")
    expect(element.shadowRoot?.querySelector("[role='alert']")).not.toBeNull()
    expect(element.shadowRoot?.textContent).toContain("Sign in to administer")
    expect(element.shadowRoot?.textContent).not.toContain("Checking whether this session")
  })

  it("distinguishes incompatible administration responses", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email"] })
      return Promise.resolve(new Response("not-json", { status: 200 }))
    })
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("server returned an incompatible response"))
    expect(element.shadowRoot?.textContent).toContain("Refresh and try again")
  })
})
