import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import axe from "axe-core"
import "./main.js"
import type { UserApp } from "./user-app.js"

const id = {
  account: "account_abcdefgh", principal: "principal_abcdefgh", tenant: "tenant_abcdefgh",
  credential: "credential_abcdefgh", session: "session_abcdefgh", lease: "lease_abcdefgh",
  event: "event_abcdefgh", reservation: "reservation_abcdefgh", rate: "rate_abcdefgh",
  producer: "producer_abcdefgh", charge: "charge_abcdefgh", link: "link_abcdefgh",
  event2: "event_ijklmnop", charge2: "charge_ijklmnop"
} as const
const instant = "2026-09-09T12:00:00Z"
const scoped = { principal_id: id.principal, tenant_id: id.tenant, account_id: id.account, roles: ["credential_holder"], expires_at: instant }
const unscoped = { principal_id: id.principal, roles: [], expires_at: instant }
const account = { id: id.account, tenant_id: id.tenant, display_name: "Studio", unit: "wei", exposure_cap: "1000", status: "active", created_at: instant }
const balance = { account_id: id.account, posted: { amount: "900", unit: "wei" }, open_lease_exposure: { amount: "100", unit: "wei" }, available: { amount: "800", unit: "wei" } }
const credential = { id: id.credential, account_id: id.account, principal_id: id.principal, prefix: "och_1234", label: "Build", status: "active", created_at: instant }
const lease = { id: id.lease, cap: "100", available: "90", pending: "5", settled: "5", unit: "wei", expires_at: instant }
const signer = { id: id.session, signer_url: "https://signer.test", discovery_url: "https://signer.test/discovery", expires_at: instant, lease }
const issuedSigner = { ...signer, token: "s".repeat(40) }
const catalog = { capability: "video.generate", model: null, rate: { numerator: "2", denominator: "1", charge_unit: "wei", quantity_unit: "fixed" }, available: true }
const usage = {
  schema_version: "1.0", event_id: id.event, reservation_id: id.reservation, lease_id: id.lease,
  tenant_id: id.tenant, account_id: id.account, principal_id: id.principal, capability: "video.generate",
  quantity: { value: "1", unit: "fixed" },
  price_snapshot: { rate_numerator: "2", rate_denominator: "1", charge_unit: "wei", quantity_unit: "fixed", source: "signer", source_version: "1" },
  producer: { id: id.producer, kind: "signer", software: "go-livepeer", software_version: "1" },
  occurred_at: instant, source: { kind: "go_livepeer_create_signed_ticket", event_id: "wire", confirmation: "kafka", signed_current_time: instant, signed_current_time_unix_ns: "1" }
}
const charge = {
  id: id.charge, usage_event_id: id.event, reservation_id: id.reservation, lease_id: id.lease,
  tenant_id: id.tenant, account_id: id.account, amount: { value: "2", unit: "wei" },
  price_snapshot: { rate_card_id: id.rate, rate_numerator: "2", rate_denominator: "1", quantity_unit: "fixed" }, created_at: instant
}
const json = (body: unknown, status = 200): Response => new Response(body === undefined ? null : JSON.stringify(body), {
  status, headers: body === undefined ? {} : { "Content-Type": "application/json" }
})

type Mode = "out" | "unscoped" | "scoped"
interface ApiOptions {
  readonly failCreateOnce?: boolean
  readonly failRefreshOnce?: boolean
}
const installApi = (initial: Mode = "scoped", options: ApiOptions = {}) => {
  let mode = initial
  let createAttempts = 0
  let refreshAttempts = 0
  const requests: Request[] = []
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(new URL(String(input), location.origin), init)
    requests.push(request)
    const { pathname } = new URL(request.url)
    if (pathname === "/v1/auth/providers") return Promise.resolve(json({ providers: ["email", "google", "github"] }))
    if (pathname === "/v1/auth/session" && request.method === "GET") return Promise.resolve(mode === "out" ? json({}, 401) : json(mode === "scoped" ? scoped : unscoped))
    if (pathname === "/v1/auth/email/code") return Promise.resolve(json(undefined, 202))
    if (pathname === "/v1/auth/email/verify") { mode = "unscoped"; return Promise.resolve(json(unscoped)) }
    if (pathname === "/v1/auth/identity-links") { mode = "out"; return Promise.resolve(json({ id: id.link, provider: "email", principal_id: id.principal, tenant_id: id.tenant, linked_at: instant })) }
    if (pathname === "/v1/auth/session/refresh") return Promise.resolve(json(scoped))
    if (pathname === "/v1/auth/session" && request.method === "DELETE") { mode = "out"; return Promise.resolve(json(undefined, 204)) }
    if (pathname === `/v1/accounts/${id.account}`) return Promise.resolve(json(account))
    if (pathname === `/v1/balances/${id.account}`) return Promise.resolve(json(balance))
    if (pathname === "/v1/credentials" && request.method === "GET") return Promise.resolve(json([credential]))
    if (pathname === "/v1/credentials" || pathname.endsWith("/rotate")) return Promise.resolve(json({ credential, secret: "c".repeat(40) }, pathname.endsWith("/rotate") ? 200 : 201))
    if (pathname.startsWith("/v1/credentials/")) return Promise.resolve(json(undefined, 204))
    if (pathname === "/v1/catalog") return Promise.resolve(json([catalog]))
    if (pathname === "/v1/sessions" && request.method === "GET") return Promise.resolve(json({ items: [signer] }))
    if (pathname === "/v1/sessions" && request.method === "POST") {
      createAttempts += 1
      return Promise.resolve(options.failCreateOnce === true && createAttempts === 1
        ? json({}, 503)
        : json(issuedSigner, 201))
    }
    if (pathname.endsWith("/refresh")) {
      refreshAttempts += 1
      return Promise.resolve(options.failRefreshOnce === true && refreshAttempts === 1
        ? json({}, 503)
        : json(issuedSigner, 201))
    }
    if (pathname.startsWith("/v1/sessions/")) return Promise.resolve(json(undefined, 204))
    if (pathname === "/v1/usage") return Promise.resolve(request.url.includes("cursor=")
      ? json({ items: [{ ...usage, event_id: id.event2, source: { ...usage.source, event_id: "wire-2" } }], page: { next_cursor: null } })
      : json({ items: [usage], page: { next_cursor: "usage_cursor_2" } }))
    if (pathname === "/v1/charges") return Promise.resolve(request.url.includes("cursor=")
      ? json({ items: [{ ...charge, id: id.charge2, usage_event_id: id.event2 }], page: { next_cursor: null } })
      : json({ items: [charge], page: { next_cursor: "charge_cursor_2" } }))
    return Promise.resolve(json({}, 404))
  })
  vi.stubGlobal("fetch", fetchMock)
  return { requests, setMode: (next: Mode) => { mode = next } }
}
const mount = (): UserApp => {
  const element = document.createElement("user-app") as UserApp
  document.body.append(element)
  return element
}
const waitForText = (element: UserApp, text: string) => vi.waitFor(() => {
  expect(element.shadowRoot?.textContent).toContain(text)
})
const submit = (element: UserApp, selector: string, values: Readonly<Record<string, string>>) => {
  const form = element.shadowRoot?.querySelector<HTMLFormElement>(selector)
  expect(form).not.toBeNull()
  for (const [name, value] of Object.entries(values)) {
    const input = form?.elements.namedItem(name)
    if (input instanceof HTMLInputElement) input.value = value
  }
  form?.requestSubmit()
}

describe("user application journeys", () => {
  beforeEach(() => {
    history.replaceState({}, "", "/")
    document.cookie = "och_csrf=csrf-value; Path=/"
  })
  afterEach(() => {
    document.body.replaceChildren()
    document.cookie = "och_csrf=; Max-Age=0; Path=/"
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("discovers sign-in providers, requests OTP, verifies, and redeems an identity invitation", async () => {
    installApi("out")
    const element = mount()
    await waitForText(element, "Sign in")
    const shell = element.shadowRoot?.querySelector("och-app-shell")
    await shell?.updateComplete
    expect(shell?.shadowRoot?.querySelectorAll("main")).toHaveLength(1)
    expect(element.shadowRoot?.querySelectorAll("a[href='/v1/auth/oauth/google/start']")).toHaveLength(1)
    expect(element.shadowRoot?.querySelectorAll("a[href='/v1/auth/oauth/github/start']")).toHaveLength(1)
    expect(element.shadowRoot?.querySelector("[class], [style]")).toBeNull()
    expect(element.shadowRoot?.querySelectorAll("input:not([id]), label:not([for])")).toHaveLength(0)
    const accessibility = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] }
    })
    expect(accessibility.violations).toEqual([])

    submit(element, "form", { email: "person@example.org" })
    await waitForText(element, "one-time code has been sent")
    submit(element, "form:has(#verify-code)", { email: "person@example.org", code: "123456" })
    await waitForText(element, "Connect your account")
    submit(element, "form", { invitation: "och_inv_" + "x".repeat(50) })
    await waitForText(element, "Sign in again")
    expect(element.shadowRoot?.textContent).not.toContain("och_inv_")
  })

  it("navigates account, catalog, usage, charges, and profile using server-derived account scope", async () => {
    const api = installApi()
    const element = mount()
    await waitForText(element, "Studio")
    const overviewLink = element.shadowRoot?.querySelector("a[href='/']")
    expect(overviewLink).not.toBeNull()
    if (overviewLink !== null && overviewLink !== undefined) expect(getComputedStyle(overviewLink).boxSizing).toBe("border-box")
    expect(element.shadowRoot?.textContent).toContain("800 wei")
    expect(element.shadowRoot?.querySelector("a[href='/']")?.getAttribute("aria-current")).toBe("page")
    expect(element.shadowRoot?.querySelector("a[href='/usage']")?.getAttribute("aria-current")).toBe("false")
    expect(element.shadowRoot?.querySelector("[slot='context']")?.textContent).toContain("Studio")
    expect(element.shadowRoot?.querySelector("[slot='utility']")?.textContent).toContain("Authenticated")
    const navigationIcons = element.shadowRoot?.querySelectorAll<SVGElement>("svg[part~='navigation-icon']") ?? []
    expect(navigationIcons).toHaveLength(7)
    expect(new Set(Array.from(navigationIcons, (icon) => icon.dataset.icon)).size).toBe(7)
    for (const icon of navigationIcons) {
      expect(icon.getAttribute("aria-hidden")).toBe("true")
      expect(icon.getAttribute("focusable")).toBe("false")
      expect(icon.querySelector("path, circle, rect")).not.toBeNull()
    }
    expect(element.shadowRoot?.querySelector("[class], [style]")).toBeNull()
    const accessibility = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] }
    })
    expect(accessibility.violations).toEqual([])
    for (const route of ["catalog", "usage", "charges", "profile"] as const) {
      const anchor = element.shadowRoot?.querySelector<HTMLAnchorElement>(`a[href='/${route}']`)
      anchor?.click()
      await waitForText(element, route === "catalog" ? "video.generate" : route === "usage" ? id.event : route === "charges" ? "2 wei" : id.principal)
      expect(anchor?.getAttribute("aria-current")).toBe("page")
    }
    expect(api.requests.filter((request) => request.url.includes("account_id=")).every((request) => request.url.includes(id.account))).toBe(true)
    const tables = element.shadowRoot?.querySelectorAll("table")
    for (const table of tables ?? []) expect(table.querySelector("caption")).not.toBeNull()
  })

  it("loads bounded non-overlapping usage and charge pages with canonical cursors", async () => {
    const api = installApi()
    const element = mount()
    await waitForText(element, "Studio")

    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/usage']")?.click()
    await waitForText(element, id.event)
    const usageMore = element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='load-more-usage']")
    usageMore?.click()
    usageMore?.click()
    await waitForText(element, id.event2)
    expect(element.shadowRoot?.textContent?.match(new RegExp(id.event2, "gu"))).toHaveLength(1)
    const usageRequests = api.requests.filter((request) => new URL(request.url).pathname === "/v1/usage")
    expect(usageRequests).toHaveLength(2)
    expect(new URL(usageRequests[0]?.url ?? location.href).search).toBe(`?account_id=${id.account}&limit=50`)
    expect(new URL(usageRequests[1]?.url ?? location.href).search).toBe(
      `?account_id=${id.account}&limit=50&cursor=usage_cursor_2`
    )
    expect(element.shadowRoot?.querySelector("[data-action='load-more-usage']")).toBeNull()

    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/charges']")?.click()
    await waitForText(element, "2 wei")
    const chargesMore = element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='load-more-charges']")
    chargesMore?.click()
    chargesMore?.click()
    await waitForText(element, id.event2)
    expect(element.shadowRoot?.textContent?.match(new RegExp(id.event2, "gu"))).toHaveLength(1)
    const chargeRequests = api.requests.filter((request) => new URL(request.url).pathname === "/v1/charges")
    expect(chargeRequests).toHaveLength(2)
    expect(new URL(chargeRequests[1]?.url ?? location.href).search).toBe(
      `?account_id=${id.account}&limit=50&cursor=charge_cursor_2`
    )
    expect(element.shadowRoot?.querySelector("[data-action='load-more-charges']")).toBeNull()
  })

  it("creates, rotates, and revokes credentials while clearing one-time secrets", async () => {
    installApi()
    vi.spyOn(window, "confirm").mockReturnValue(true)
    const element = mount()
    await waitForText(element, "Studio")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/credentials']")?.click()
    await waitForText(element, "Build")
    submit(element, "form", { label: "Deployment" })
    await waitForText(element, "Credential created")
    expect(element.shadowRoot?.querySelector("dialog")?.open).toBe(true)
    expect(element.shadowRoot?.querySelector("[data-secret]")?.textContent).toHaveLength(40)
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='close-secret']")?.click()
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("[data-secret]")).toBeNull())
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='rotate-credential']")?.click()
    await waitForText(element, "Credential rotated")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/profile']")?.click()
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("[data-secret]")).toBeNull())
    expect(location.href).not.toContain("c".repeat(40))
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/credentials']")?.click()
    await waitForText(element, "Build")
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='revoke-credential']")?.click()
    await vi.waitFor(() => expect(window.confirm).toHaveBeenCalled())
  })

  it("creates, refreshes, and revokes bounded signer sessions with explicit confirmation", async () => {
    const api = installApi()
    vi.spyOn(window, "confirm").mockReturnValue(true)
    const element = mount()
    await waitForText(element, "Studio")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/sessions']")?.click()
    await waitForText(element, id.session)
    submit(element, "form", { capability: "video.generate", model: "", app: "demo", cap: "100", ttl: "3600" })
    await waitForText(element, "Signer session created")
    expect(api.requests.find((request) => request.method === "POST" && new URL(request.url).pathname === "/v1/sessions")?.headers.has("Idempotency-Key")).toBe(true)
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='close-secret']")?.click()
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("[data-secret]")).toBeNull())
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='refresh-session']")?.click()
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Signer session refreshed"), {
      timeout: 3_000
    })
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='close-secret']")?.click()
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='revoke-session']")?.click()
    await vi.waitFor(() => expect(window.confirm).toHaveBeenCalled())
  })

  it("reuses create idempotency across failure retry and blocks reentrant submits", async () => {
    const api = installApi("scoped", { failCreateOnce: true })
    const element = mount()
    await waitForText(element, "Studio")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/sessions']")?.click()
    await waitForText(element, id.session)
    const values = { capability: "video.generate", model: "", app: "retry", cap: "100", ttl: "3600" }

    submit(element, "form", values)
    submit(element, "form", values)
    await vi.waitFor(() => expect(api.requests.filter((request) => request.method === "POST"
      && new URL(request.url).pathname === "/v1/sessions")).toHaveLength(1))
    const submitButton = element.shadowRoot?.querySelector<HTMLButtonElement>("form button[type='submit']")
    await vi.waitFor(() => expect(submitButton?.disabled).toBe(false))
    submit(element, "form", values)
    await waitForText(element, "Signer session created")
    const firstCommand = api.requests.filter((request) => request.method === "POST"
      && new URL(request.url).pathname === "/v1/sessions")
    expect(firstCommand).toHaveLength(2)
    expect(firstCommand[0]?.headers.get("Idempotency-Key")).toBe(firstCommand[1]?.headers.get("Idempotency-Key"))

    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='close-secret']")?.click()
    submit(element, "form", values)
    await waitForText(element, "Signer session created")
    const successfulNextCommand = api.requests.filter((request) => request.method === "POST"
      && new URL(request.url).pathname === "/v1/sessions")
    expect(successfulNextCommand).toHaveLength(3)
    expect(successfulNextCommand[2]?.headers.get("Idempotency-Key")).not.toBe(
      successfulNextCommand[1]?.headers.get("Idempotency-Key")
    )
  })

  it("reuses refresh idempotency across failure retry and blocks reentrant actions", async () => {
    const api = installApi("scoped", { failRefreshOnce: true })
    vi.spyOn(window, "confirm").mockReturnValue(true)
    const element = mount()
    await waitForText(element, "Studio")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/sessions']")?.click()
    await waitForText(element, id.session)
    const refresh = () => element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='refresh-session']")

    refresh()?.click()
    refresh()?.click()
    await vi.waitFor(() => expect(api.requests.filter((request) => new URL(request.url).pathname.endsWith("/refresh"))).toHaveLength(1))
    await waitForText(element, "could not complete")
    await vi.waitFor(() => expect(refresh()?.disabled).toBe(false))
    refresh()?.click()
    await vi.waitFor(() => expect(api.requests.filter((request) => new URL(request.url).pathname.endsWith("/refresh"))).toHaveLength(2))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Signer session refreshed"), {
      timeout: 3_000
    })
    const retries = api.requests.filter((request) => new URL(request.url).pathname.endsWith("/refresh"))
    expect(retries).toHaveLength(2)
    expect(retries[0]?.headers.get("Idempotency-Key")).toBe(retries[1]?.headers.get("Idempotency-Key"))

    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='close-secret']")?.click()
    await vi.waitFor(() => expect(element.shadowRoot?.querySelector("[data-secret]")).toBeNull())
    await vi.waitFor(() => expect(refresh()?.disabled).toBe(false))
    refresh()?.click()
    await vi.waitFor(() => expect(api.requests.filter((request) => new URL(request.url).pathname.endsWith("/refresh"))).toHaveLength(3))
    await vi.waitFor(() => expect(element.shadowRoot?.textContent).toContain("Signer session refreshed"), {
      timeout: 3_000
    })
    const nextCommand = api.requests.filter((request) => new URL(request.url).pathname.endsWith("/refresh"))
    expect(nextCommand).toHaveLength(3)
    expect(nextCommand[2]?.headers.get("Idempotency-Key")).not.toBe(nextCommand[1]?.headers.get("Idempotency-Key"))
  })

  it("refreshes and ends the browser session and clears secret state on removal", async () => {
    installApi()
    const element = mount()
    await waitForText(element, "Studio")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/profile']")?.click()
    await waitForText(element, "Profile and security")
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='refresh-browser']")?.click()
    await waitForText(element, "Browser session refreshed")
    element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='logout']")?.click()
    await waitForText(element, "Signed out")
    element.remove()
  })

  it("presents rate-limit, permission, and generic failures without exposing response details", async () => {
    let status = 429
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      if (path === "/v1/auth/providers") return Promise.resolve(json({ providers: ["email"] }))
      if (path === "/v1/auth/session") return Promise.resolve(json({}, 401))
      return Promise.resolve(json({ detail: "private backend detail" }, status))
    }))
    const element = mount()
    await waitForText(element, "Sign in")
    submit(element, "form", { email: "person@example.org" })
    await waitForText(element, "Too many attempts")
    expect(element.shadowRoot?.textContent).not.toContain("private backend detail")
    status = 403
    submit(element, "form", { email: "person@example.org" })
    await waitForText(element, "not permitted")
  })

  it("renders unavailable, modeled, inactive, and cancelled-action branches and handles history", async () => {
    history.replaceState({}, "", "/not-a-route")
    vi.spyOn(window, "confirm").mockReturnValue(false)
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(new URL(String(input), location.origin), init)
      const { pathname } = new URL(request.url)
      if (pathname === "/v1/auth/providers") return Promise.resolve(json({ providers: ["email"] }))
      if (pathname === "/v1/auth/session") return Promise.resolve(json(scoped))
      if (pathname === `/v1/accounts/${id.account}`) return Promise.resolve(json(account))
      if (pathname === `/v1/balances/${id.account}`) return Promise.resolve(json(balance))
      if (pathname === "/v1/catalog") return Promise.resolve(json([{ ...catalog, model: "fast", available: false }]))
      if (pathname === "/v1/usage") return Promise.resolve(json({ items: [{ ...usage, model: "fast" }], page: { next_cursor: null } }))
      if (pathname === "/v1/credentials") return Promise.resolve(json([{ ...credential, status: "revoked" }]))
      return Promise.resolve(json(undefined, 204))
    }))
    const element = mount()
    await waitForText(element, "Studio")
    expect(history.state).toEqual({})
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/catalog']")?.click()
    await waitForText(element, "Unavailable")
    expect(element.shadowRoot?.textContent).toContain("fast")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/usage']")?.click()
    await waitForText(element, "video.generate — fast")
    element.shadowRoot?.querySelector<HTMLAnchorElement>("a[href='/credentials']")?.click()
    await waitForText(element, "revoked")
    expect(element.shadowRoot?.querySelector<HTMLButtonElement>("[data-action='revoke-credential']")?.disabled).toBe(true)
    window.dispatchEvent(new PopStateEvent("popstate"))
    await element.updateComplete
  })

  it("ignores malformed or inapplicable DOM events at component boundaries", async () => {
    installApi()
    const element = mount()
    await waitForText(element, "Studio")
    const harness = element as unknown as {
      navigate: (event: Event) => void
      requestCode: (event: SubmitEvent) => void
      verifyCode: (event: SubmitEvent) => void
      redeem: (event: SubmitEvent) => void
      issueCredential: (event: SubmitEvent) => void
      createSession: (event: SubmitEvent) => void
      handleAction: (event: Event) => void
      loadRoute: () => void
      auth: unknown
    }
    const malformedSubmit = { preventDefault: vi.fn(), currentTarget: null } as unknown as SubmitEvent
    harness.navigate(new Event("click"))
    harness.requestCode(malformedSubmit)
    harness.verifyCode(malformedSubmit)
    harness.redeem(malformedSubmit)
    harness.issueCredential(malformedSubmit)
    harness.createSession(malformedSubmit)
    harness.handleAction(new Event("click"))
    const button = document.createElement("button")
    harness.handleAction({ target: button } as unknown as Event)
    harness.auth = { tag: "signedOut" }
    harness.loadRoute()
    expect(malformedSubmit.preventDefault).toHaveBeenCalledTimes(5)
  })

  it("distinguishes denied and unavailable session checks", async () => {
    let status = 403
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), location.origin).pathname
      return Promise.resolve(path === "/v1/auth/providers" ? json({ providers: ["email"] }) : json({}, status))
    }))
    const denied = mount()
    await waitForText(denied, "Access denied")
    denied.remove()
    status = 503
    const unavailable = mount()
    await waitForText(unavailable, "Service unavailable")
  })
})
