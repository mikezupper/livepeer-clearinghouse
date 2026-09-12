/* eslint-disable @typescript-eslint/no-non-null-assertion -- test fixtures assert rendered DOM */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import "./admin-app.js"
import type { AdminApp } from "./admin-app.js"

const at = "2026-09-11T14:00:00+00:00"
const session = { user_id: "usr_admin", account_id: "acct_admin", email: "admin@example.com", is_admin: true, expires_at: at }
const workload = { id: "work_123", account_id: "acct_user", capability: "live", model: "noop", offer_id: "price_123", quoted_price: { numerator: "2", denominator: "1", currency: "wei", quantity_unit: "pixel" }, status: "active", client_reference: "job-1", runner_session_id: null, manifest_id: null, payment_session_id: null, created_at: at, expires_at: at }
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
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("one-time code has been sent"))
    expect(fetchMock.mock.calls.filter(([input]) => new URL(String(input), location.origin).pathname === "/v1/auth/email/code")).toHaveLength(1)
    expect(element.shadowRoot?.textContent).toContain("Continue with github")
    const verify = element.shadowRoot!.querySelectorAll("form")[1]!
    const fields = verify.querySelectorAll<HTMLInputElement>("input")
    fields[0]!.value = "admin@example.com"
    fields[1]!.value = "123456"
    verify.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Clearinghouse overview"))
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
    expect(element.shadowRoot?.textContent).toContain("user@example.com")
    await navigate("workloads", "Workloads")
    expect(element.shadowRoot?.textContent).toContain("work_123")
    expect(element.shadowRoot?.textContent).toContain("Expired")
    expect(element.shadowRoot?.textContent).toContain("Access ends")
    await navigate("usage", "Usage attribution")
    expect(element.shadowRoot?.textContent).toContain("Unmatched events")
    await navigate("operations", "Operations")
    const stop = element.shadowRoot!.querySelector("form")!
    stop.querySelector<HTMLInputElement>("#reason")!.value = "maintenance"
    stop.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("authorization stopped"))
    const resume = element.shadowRoot!.querySelector("form")!
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

  it("leaves session checking when bootstrap transport fails", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error("offline")))
    const element = document.createElement("admin-app") as AdminApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Administration data is unavailable"))
    expect(element.shadowRoot?.textContent).toContain("Sign in to administer")
    expect(element.shadowRoot?.textContent).not.toContain("Checking administrator access")
  })
})
