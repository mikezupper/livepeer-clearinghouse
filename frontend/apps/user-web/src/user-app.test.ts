/* eslint-disable @typescript-eslint/no-non-null-assertion -- test fixtures assert rendered DOM */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import "./user-app.js"
import type { UserApp } from "./user-app.js"

const at = "2026-09-11T14:00:00+00:00"
const session = { user_id: "usr_123", account_id: "acct_123", email: "user@example.com", is_admin: false, expires_at: at }
const price = { numerator: "2", denominator: "1", currency: "wei", quantity_unit: "pixel" }
const offer = { id: "price_123", runner_url: "https://runner.example.com", orchestrator_address: null, capability: "live", model: "noop", constraints: {}, price, observed_at: at, expires_at: at }
const secondsOffer = { ...offer, id: "price_seconds", capability: "stream", price: { ...price, numerator: "5", denominator: "2", quantity_unit: "seconds" } }
const unsupportedOffer = { ...offer, id: "price_unknown", capability: "future", price: { ...price, quantity_unit: "requests-per-fortnight" } }
const workload = { id: "work_123", account_id: session.account_id, capability: "live", model: "noop", offer_id: offer.id, quoted_price: price, status: "active", client_reference: "job-1", runner_session_id: null, manifest_id: null, payment_session_id: null, created_at: at, expires_at: at }

const reply = (body?: unknown, status = 200): Promise<Response> => Promise.resolve(new Response(
  body === undefined ? null : JSON.stringify(body),
  { status, headers: body === undefined ? {} : { "Content-Type": "application/json" } }
))

describe("user application", () => {
  let signedIn = false
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    history.replaceState(null, "", "/")
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
      if (path === "/v1/offers") return reply({ items: [offer, secondsOffer, unsupportedOffer], next_cursor: null })
      if (path === "/v1/workloads" && init?.method === "POST") return reply({ ...workload, token: "och_work_secret", sdk_token: "sdk-token", signer_url: "https://signer", discovery_url: "https://discovery" }, 201)
      if (path === "/v1/workloads" && init?.method === undefined) return reply({ items: [workload], next_cursor: null })
      if (path.startsWith("/v1/workloads/")) return reply(undefined, 204)
      if (path === "/v1/credentials" && init?.method === "POST") return reply({ id: "cred_123", name: "Python", token: "och_live_secret", created_at: at }, 201)
      if (path === "/v1/credentials") return reply({ items: [{ id: "cred_123", name: "Python", created_at: at, revoked_at: null }], next_cursor: null })
      if (path.startsWith("/v1/credentials/")) return reply(undefined, 204)
      if (path === "/v1/usage") return reply({ items: [{ id: "usage_123", workload_id: workload.id, manifest_id: "manifest", payment_session_id: "pm", capability: "live", quantity: "10", quantity_unit: "pixel", computed_fee: "20", currency: "wei", ticket_count: 1, sequence_number: 0, occurred_at: at, status: "matched" }], next_cursor: null })
      if (path === "/v1/costs") return reply({ items: [{ workload, measured_quantity: "10", measured_unit: "pixel", quoted_fee: "20", computed_fee: "20", currency: "wei", event_count: 1 }], next_cursor: null })
      if (path === "/v1/summary") return reply({ offers: 3, credentials: 1, workloads: 1, active_workloads: 1, usage_events: 1, computed_fee: "20", currency: "wei" })
      return reply({}, 404)
    })
    vi.stubGlobal("fetch", fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  const mount = async (): Promise<UserApp> => {
    const element = document.createElement("user-app") as UserApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("h2")?.textContent).toBe("Sign in"))
    return element
  }

  it("signs in and completes every core account workflow", async () => {
    const element = await mount()
    const forms = element.shadowRoot!.querySelectorAll("form")
    forms[0]!.querySelector<HTMLInputElement>("input")!.value = "user@example.com"
    forms[0]!.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("one-time code has been sent"))
    const verify = element.shadowRoot!.querySelectorAll("form")[1]!
    const fields = verify.querySelectorAll<HTMLInputElement>("input")
    fields[0]!.value = "user@example.com"
    fields[1]!.value = "123456"
    verify.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Clearinghouse overview"))
    expect(element.shadowRoot?.textContent).toContain("20 wei")
    const denomination = element.shadowRoot!.querySelector("och-denomination-control")!.shadowRoot!.querySelector<HTMLSelectElement>("select")!
    denomination.value = "eth"
    denomination.dispatchEvent(new Event("change", { bubbles: true }))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("0.00000000000000002 ETH"))
    expect(element.shadowRoot?.textContent).toContain("20 wei exact")
    denomination.value = "wei"
    denomination.dispatchEvent(new Event("change", { bubbles: true }))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("20 wei"))

    const navigate = async (label: string, heading: string): Promise<void> => {
      element.shadowRoot!.querySelector<HTMLAnchorElement>(`a[href$='/${label}']`)!.click()
      await vi.waitFor(() => expect(element.shadowRoot?.querySelector("h2")?.textContent).toBe(heading))
    }
    await navigate("discovery", "Capabilities and prices")
    expect(element.shadowRoot?.textContent).toContain("runner.example.com")
    await navigate("estimate", "Cost estimator")
    const estimateForm = element.shadowRoot!.querySelector<HTMLFormElement>("form[part~='form-card']")!
    const estimateField = (name: string, value: string): void => {
      const field = estimateForm.querySelector<HTMLInputElement>(`[name='${name}']`)!
      field.value = value
      field.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true }))
    }
    estimateField("width", "10")
    estimateField("height", "10")
    estimateField("frames", "2")
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("400 wei"))
    estimateForm.querySelector<HTMLInputElement>("#estimate-reference")!.value = "estimated-job"
    estimateForm.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("dialog")?.textContent).toContain("sdk-token"))
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='close-secret']")!.click()
    const estimateOffer = estimateForm.querySelector<HTMLSelectElement>("#estimate-offer")!
    estimateOffer.value = secondsOffer.id
    estimateOffer.dispatchEvent(new Event("change", { bubbles: true, composed: true }))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Expected runtime in seconds"))
    estimateField("duration", "0")
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Enter a positive duration"))
    estimateOffer.value = unsupportedOffer.id
    estimateOffer.dispatchEvent(new Event("change", { bubbles: true, composed: true }))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("not supported by this estimator"))
    await navigate("workloads", "Workloads")
    expect(element.shadowRoot?.textContent).toContain("Access ends")
    const workloadForm = element.shadowRoot!.querySelector("form")!
    workloadForm.querySelector<HTMLInputElement>("#reference")!.value = "job-1"
    workloadForm.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("dialog")?.textContent).toContain("sdk-token"))
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='close-secret']")!.click()
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='revoke-workload']")!.click()

    await navigate("credentials", "API credentials")
    const credentialForm = element.shadowRoot!.querySelector("form")!
    credentialForm.querySelector<HTMLInputElement>("input")!.value = "Python"
    credentialForm.requestSubmit()
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("dialog")?.textContent).toContain("och_live_secret"))
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='close-secret']")!.click()
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='revoke-credential']")!.click()

    await navigate("usage", "Usage and cost")
    expect(element.shadowRoot?.textContent).toContain("20 wei")
    await navigate("profile", "Profile and security")
    element.shadowRoot!.querySelector<HTMLButtonElement>("[data-action='logout']")!.click()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Email a one-time code"))
    expect(fetchMock).toHaveBeenCalled()
  })

  it("renders expired access as expired and does not offer revocation", async () => {
    signedIn = true
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (path === "/v1/auth/session") return reply(session)
      if (path === "/v1/offers") return reply({ items: [offer], next_cursor: null })
      if (path === "/v1/workloads") return reply({ items: [{ ...workload, status: "expired" }], next_cursor: null })
      if (path === "/v1/costs") return reply({ items: [], next_cursor: null })
      if (path === "/v1/summary") return reply({ offers: 1, credentials: 0, workloads: 1, active_workloads: 0, usage_events: 0, computed_fee: "0", currency: "wei" })
      return reply({}, 404)
    })
    history.replaceState(null, "", "/workloads")
    const element = document.createElement("user-app") as UserApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Expired"))
    expect(element.shadowRoot?.querySelector("[data-action='revoke-workload']")).toBeNull()
  })

  it("filters and walks network offer cursors", async () => {
    signedIn = true
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = new URL(String(input), location.origin)
      if (url.pathname === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (url.pathname === "/v1/auth/session") return reply(session)
      if (url.pathname === "/v1/offers") return reply({
        items: [offer], next_cursor: url.searchParams.has("cursor") ? null : "offer-next"
      })
      return reply({}, 404)
    })
    history.replaceState(null, "", "/discovery")
    const element = document.createElement("user-app") as UserApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("runner.example.com"))
    const filter = element.shadowRoot!.querySelector<HTMLFormElement>("search form")!
    filter.querySelector<HTMLInputElement>("[name='capability']")!.value = "live"
    filter.querySelector<HTMLInputElement>("[name='model']")!.value = "noop"
    filter.requestSubmit()
    await vi.waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = new URL(String(input), location.origin)
      return url.searchParams.get("capability") === "live" && url.searchParams.get("model") === "noop"
    })).toBe(true))
    const pagination = element.shadowRoot!.querySelector("och-cursor-pagination")!
    await pagination.updateComplete
    pagination.shadowRoot!.querySelectorAll("button")[1]!.click()
    await vi.waitFor(() => expect(location.search).toContain("cursor=offer-next"))
    await pagination.updateComplete
    pagination.shadowRoot!.querySelectorAll("button")[0]!.click()
    await vi.waitFor(() => expect(new URL(location.href).searchParams.has("cursor")).toBe(false))
    expect(new URL(location.href).searchParams.get("capability")).toBe("live")
  })

  it("keeps a large network fixture to one bounded DOM page", async () => {
    signedIn = true
    const page = Array.from({ length: 50 }, (_, index) => ({
      ...offer, id: `price_${index}`, runner_url: `https://runner-${index}.example.com`
    }))
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (path === "/v1/auth/session") return reply(session)
      if (path === "/v1/offers") return reply({ items: page, next_cursor: "more" })
      return reply({}, 404)
    })
    history.replaceState(null, "", "/discovery")
    const element = document.createElement("user-app") as UserApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot!.querySelectorAll("tbody tr")).toHaveLength(50))
    expect(element.shadowRoot!.querySelector("och-cursor-pagination")).not.toBeNull()
  })

  it("ignores a stale collection failure after rapid route navigation", async () => {
    signedIn = true
    let rejectOffers: ((reason?: unknown) => void) | undefined
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return reply({ providers: ["email"] })
      if (path === "/v1/auth/session") return reply(session)
      if (path === "/v1/offers") return new Promise<Response>((_resolve, reject) => {
        rejectOffers = reject
      })
      if (path === "/v1/credentials") return reply({
        items: [{ id: "cred_123", name: "Current route", created_at: at, revoked_at: null }],
        next_cursor: null
      })
      return reply({}, 404)
    })
    history.replaceState(null, "", "/discovery")
    const element = document.createElement("user-app") as UserApp
    document.body.append(element)
    await vi.waitFor(() => expect(rejectOffers).toBeTypeOf("function"))
    element.shadowRoot!.querySelector<HTMLAnchorElement>("a[href='/credentials']")!.click()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Current route"))
    rejectOffers?.(new Error("late failure"))
    await Promise.resolve()
    expect(element.shadowRoot?.textContent).not.toContain("could not complete")
  })

  it("falls back to overview routes and reports API failures", async () => {
    signedIn = true
    history.replaceState(null, "", "/unknown")
    fetchMock.mockImplementationOnce(() => Promise.reject(new Error("offline")))
    const element = document.createElement("user-app") as UserApp
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("could not complete"))
  })
})
