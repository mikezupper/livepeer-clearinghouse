import { Effect, Schema } from "effect"
import axe from "axe-core"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { AdminApiShape, Mutation } from "./api.js"
import "./admin-app.js"
import type { AdminApp } from "./admin-app.js"
import { ApiProblem, Overview, TransportFailure } from "./domain.js"

const at = "2026-09-09T12:00:00Z"
export const overviewPayload = { session: { principal_id: "principal_12345678", roles: ["operator"], expires_at: at }, tenants: [{ id: "tenant_12345678", display_name: "Video team", status: "active", created_at: at }], accounts: [{ id: "account_12345678", tenant_id: "tenant_12345678", display_name: "Production", unit: "wei", exposure_cap: "100", status: "active", created_at: at }], principals: [{ id: "principal_12345678", tenant_id: "tenant_12345678", account_id: "account_12345678", display_name: "Operator", roles: ["operator"], status: "active", created_at: at }], balances: [{ account_id: "account_12345678", posted: { amount: "75", unit: "wei" }, open_lease_exposure: { amount: "10", unit: "wei" }, available: { amount: "65", unit: "wei" } }], grants: [{ id: "grant_12345678", account_id: "account_12345678", kind: "credit", amount: { amount: "75", unit: "wei" }, reason: "Launch", external_reference: null, actor_id: "principal_12345678", created_at: at }], leases: [{ id: "lease_12345678", cap: "10", available: "4", pending: "2", settled: "4", unit: "wei", expires_at: at }], usage: [{ schema_version: "1.0", event_id: "usage_12345678", reservation_id: "receipt_12345678", lease_id: "lease_12345678", tenant_id: "tenant_12345678", account_id: "account_12345678", principal_id: "principal_12345678", capability: "live", quantity: { value: "2", unit: "seconds" }, price_snapshot: { rate_numerator: "1", rate_denominator: "1", charge_unit: "wei", quantity_unit: "seconds", source: "signer", source_version: "1" }, producer: { id: "signer_12345678", kind: "signer", software: "go-livepeer", software_version: "e8dcf7a" }, occurred_at: at, source: { kind: "go_livepeer_create_signed_ticket", event_id: "event-1", confirmation: "kafka", signed_current_time: at, signed_current_time_unix_ns: "1788955200000000000" } }], charges: [{ id: "charge_12345678", usage_event_id: "usage_12345678", reservation_id: "receipt_12345678", lease_id: "lease_12345678", tenant_id: "tenant_12345678", account_id: "account_12345678", amount: { value: "2", unit: "wei" }, price_snapshot: { rate_card_id: "rate_12345678", rate_numerator: "1", rate_denominator: "1", quantity_unit: "seconds" }, created_at: at }], reservations: [{ id: "receipt_12345678", lease_id: "lease_12345678", tenant_id: "tenant_12345678", account_id: "account_12345678", status: "pending", reserved_amount: { value: "2", unit: "wei" }, sequence_number: "1", signer_confirmed_at: null, created_at: at }], rates: [{ id: "rate_12345678", version: "1", capability: "live", model: null, rate: { numerator: "1", denominator: "1", charge_unit: "wei", quantity_unit: "seconds" }, effective_at: at, created_at: at }], metering: { status: "ready", open_cases: 1, quarantined: 0, unresolved: 1, global_exposure_cap: "1000", global_open_exposure: "10", last_checkpoint_at: at, last_heartbeat_at: at }, reconciliation: [{ id: "case_12345678", reservation_id: "receipt_12345678", tenant_id: "tenant_12345678", account_id: "account_12345678", kind: "missing_confirmation", status: "open", reason: "missing_event", created_at: at, resolved_at: null }], audit: [{ id: "audit_12345678", actor_id: "principal_12345678", action: "grant.created", target_id: "grant_12345678", reason: "Launch", request_id: "request-1", occurred_at: at }], adapters: [{ manifest_version: "1.0", name: "core", version: "1.0.0", source: "builtin", builtin: { selector: "core" }, ports: [{ name: "metering", contract_version: "1", capabilities: ["ingest"] }] }], killSwitch: { enabled: false, reason: "Normal operation", changed_at: at, actor_id: null } } as const
const overview = Schema.decodeUnknownSync(Overview)(overviewPayload)

const settle = (element: AdminApp) =>
  Promise.resolve()
    .then(() => element.updateComplete)
    .then(() => Promise.resolve())
    .then(() => element.updateComplete)

const mount = (overrides: Partial<AdminApiShape>) => {
  const api: AdminApiShape = { overview: Effect.succeed(overview), providers: Effect.succeed(["email"]), requestEmailCode: () => Effect.void, verifyEmailCode: () => Effect.void, refreshSession: Effect.void, logout: Effect.void, issueInvitation: () => Effect.succeed("och_inv_123456789012345678901234567890123456789012"), mutate: () => Effect.void, ...overrides }
  const element = document.createElement("admin-app") as AdminApp
  element.api = api
  document.body.append(element)
  return settle(element).then(() => element)
}

const setForm = (form: HTMLFormElement, values: Readonly<Record<string, string>>) => {
  Object.entries(values).forEach(([name, value]) => {
    const control = form.elements.namedItem(name)
    if (control instanceof HTMLInputElement || control instanceof HTMLTextAreaElement || control instanceof HTMLSelectElement) control.value = value
  })
}

const confirmChange = (element: AdminApp) => {
  const dialog = element.shadowRoot?.querySelector<HTMLDialogElement>("#change-confirmation")
  expect(dialog?.querySelector(":focus")?.getAttribute("value")).toBe("cancel")
  dialog?.querySelector("form")?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
}

const navigate = (element: AdminApp, route: string) => {
  const link = element.shadowRoot?.querySelector<HTMLAnchorElement>(`[data-route="${route}"]`)
  expect(link).toBeInstanceOf(HTMLAnchorElement)
  link?.click()
  return settle(element).then(() => element)
}

afterEach(() => {
  document.body.replaceChildren()
  localStorage.clear()
  sessionStorage.clear()
  window.history.replaceState(null, "", "/")
  vi.restoreAllMocks()
})

describe("admin application", () => {
  it("has no automated accessibility violations in a real browser", () =>
    mount({}).then(() =>
      axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } }).then((result) => {
        expect(result.violations).toEqual([])
      })
    ))

  it("renders authoritative operator data in semantic route-specific views", () =>
    mount({ overview: Effect.succeed(overview), mutate: () => Effect.void }).then((element) => {
      const root = element.shadowRoot
      expect(root?.querySelector("[style]")).toBe(null)
      expect(root?.querySelectorAll("[data-route]")).toHaveLength(9)
      const icons = root?.querySelectorAll<SVGElement>("svg[part~='navigation-icon']")
      expect(icons).toHaveLength(9)
      expect(Array.from(icons ?? []).every((icon) => icon.getAttribute("aria-hidden") === "true" && icon.getAttribute("focusable") === "false")).toBe(true)
      expect(root?.querySelectorAll("[part~='badge-success']").length).toBeGreaterThan(0)
      expect(root?.querySelector("[aria-current='page']")?.getAttribute("data-route")).toBe("overview")
      let tables = 0
      return ["tenants", "accounts", "principals", "financials", "policies", "usage", "operations", "audit"]
        .reduce<Promise<void>>(
          (previous, route) =>
            previous
              .then(() => navigate(element, route))
              .then(() => {
                tables += root?.querySelectorAll("table").length ?? 0
                expect(root?.querySelector("[aria-current='page']")?.getAttribute("data-route")).toBe(route)
              }),
          Promise.resolve()
        )
        .then(() => {
          expect(tables).toBe(14)
          expect(window.location.pathname).toBe("/audit")
        })
    }))

  it("preserves direct and admin mounts through History API navigation", () => {
    window.history.replaceState(null, "", "/admin/usage/")
    return mount({})
      .then((element) => {
        expect(element.shadowRoot?.querySelector("[aria-current='page']")?.getAttribute("data-route")).toBe("usage")
        expect(element.shadowRoot?.querySelector("#usage time")?.textContent).toBe(at)
        return navigate(element, "operations")
      })
      .then((element) => {
        expect(window.location.pathname).toBe("/admin/operations")
        window.history.pushState(null, "", "/admin/accounts")
        window.dispatchEvent(new PopStateEvent("popstate"))
        return settle(element).then(() => element)
      })
      .then((element) => {
        expect(element.shadowRoot?.querySelector("[aria-current='page']")?.getAttribute("data-route")).toBe("accounts")
        expect(element.shadowRoot?.querySelector("#balances")?.textContent).toContain("65 wei")
      })
  })

  it("renders tenant scope and empty records without operator controls", () => {
    const tenant = { ...overview, session: { ...overview.session, tenant_id: "tenant_12345678" as (typeof overview.accounts)[number]["tenant_id"], roles: ["tenant_admin"] as const }, tenants: [], accounts: [], principals: overview.principals.map((principal) => ({ ...principal, tenant_id: null, account_id: null, display_name: null, roles: [] })), balances: [], grants: [], leases: [], usage: [], charges: [], reservations: [], rates: [], reconciliation: [], audit: [], adapters: null, metering: null, killSwitch: null }
    return mount({ overview: Effect.succeed(tenant), mutate: () => Effect.void })
      .then((element) => {
        expect(element.shadowRoot?.querySelector("#emergency")).toBe(null)
        expect(element.shadowRoot?.querySelector("[part~='metrics']")?.textContent).toContain("Tenant scope")
        expect(element.shadowRoot?.querySelector("[part~='scope']")?.textContent).toContain("Tenant administrator")
        return navigate(element, "principals")
      })
      .then((element) => {
        expect(element.shadowRoot?.querySelector("#principals")?.textContent).toContain("Unnamed principal")
        expect(element.shadowRoot?.querySelector("#principals")?.textContent).toContain("Unscoped")
        expect(element.shadowRoot?.querySelector("#principals")?.textContent).toContain("No roles")
        expect(element.shadowRoot?.querySelector("#controls")?.textContent).not.toContain("Create tenant")
        expect(element.shadowRoot?.querySelector("#principal-role")?.textContent).not.toContain("operator")
        return navigate(element, "accounts")
      })
      .then((element) => {
        expect(element.shadowRoot?.querySelector<HTMLInputElement>("#account-tenant")?.readOnly).toBe(true)
        expect(element.shadowRoot?.querySelector<HTMLInputElement>("#account-tenant")?.value).toBe("tenant_12345678")
        return navigate(element, "policies")
      })
      .then((element) => {
        expect(element.shadowRoot?.querySelector("#controls")?.textContent).not.toContain("Publish rate card")
        return navigate(element, "audit")
      })
      .then((element) => {
        expect(element.shadowRoot?.querySelector("#audit tbody")?.textContent).toContain("No records")
      })
  })

  it("submits tenant, account, principal, and exact-rate forms", () => {
    const commands: Mutation[] = []
    return mount({
      overview: Effect.succeed(overview),
      mutate: (command) =>
        Effect.sync(() => {
          commands.push(command)
        })
    }).then((element) => {
      const submit = (route: string, selector: string, values: Readonly<Record<string, string>>, confirm = false) =>
        navigate(element, route).then(() => {
          const form = element.shadowRoot?.querySelector<HTMLFormElement>(selector)
          expect(form).toBeInstanceOf(HTMLFormElement)
          if (form !== null && form !== undefined) setForm(form, values)
          form?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
          if (confirm) confirmChange(element)
          return settle(element)
        })
      return submit("tenants", "form:has(#tenant-name)", { display_name: "New tenant" })
        .then(() => submit("accounts", "form:has(#account-tenant)", { tenant_id: "tenant_12345678", display_name: "New account", unit: "wei", exposure_cap: "50" }))
        .then(() => submit("principals", "form:has(#principal-name)", { display_name: "", tenant_id: "tenant_12345678", account_id: "" }))
        .then(() => submit("policies", "form:has(#rate-capability)", { capability: "live", model: "", numerator: "2", denominator: "3", charge_unit: "wei", effective_at: "2026-09-09T12:00" }, true))
        .then(() => {
          expect(commands.map((command) => command.path)).toEqual(["/v1/tenants", "/v1/accounts", "/v1/principals", "/v1/rate-cards"])
          expect(commands[2]?.body).toMatchObject({ account_id: null, display_name: null, roles: ["credential_holder"] })
          expect(commands[3]?.body).toMatchObject({ model: null })
          expect(String(commands[3]?.body.effective_at)).toMatch(/^2026-09-09T/u)
        })
    })
  })

  it("surfaces typed load failures and retries", () => {
    let attempts = 0
    const api: Partial<AdminApiShape> = { overview: Effect.suspend(() => (++attempts === 1 ? Effect.fail(new TransportFailure({ operation: "overview", cause: "offline" })) : Effect.succeed(overview))), mutate: () => Effect.void }
    return mount(api).then((element) => {
      expect(element.shadowRoot?.querySelector("[role='alert']")?.textContent).toContain("unavailable")
      element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='state'] button")?.click()
      return settle(element).then(() => {
        expect(element.shadowRoot?.querySelector("#overview")).not.toBe(null)
        expect(attempts).toBe(2)
      })
    })
  })

  it("discovers sign-in providers and completes the email-code journey", () => {
    const requestEmailCode = vi.fn(() => Effect.void)
    const verifyEmailCode = vi.fn(() => Effect.void)
    return mount({ overview: Effect.fail(new ApiProblem({ operation: "session", status: 401, title: "Sign in required" })), providers: Effect.succeed(["email", "google", "github"]), requestEmailCode, verifyEmailCode }).then((element) =>
      settle(element)
        .then(() => {
          const signIn = element.shadowRoot?.querySelector("#sign-in")
          expect(signIn?.querySelectorAll("a")).toHaveLength(2)
          const form = signIn?.querySelector<HTMLFormElement>("form")
          if (form !== null && form !== undefined) setForm(form, { email: "admin@example.com" })
          form?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
          return settle(element)
        })
        .then(() => {
          expect(requestEmailCode).toHaveBeenCalledWith("admin@example.com")
          const codeForm = element.shadowRoot?.querySelector<HTMLFormElement>("#sign-in form")
          if (codeForm !== null && codeForm !== undefined) setForm(codeForm, { code: "123456" })
          codeForm?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
          return settle(element)
        })
        .then(() => {
          expect(verifyEmailCode).toHaveBeenCalledWith("admin@example.com", "123456")
        })
    )
  })

  it("falls back to email when provider discovery fails and supports OAuth-only deployments", () => {
    const unauthorized = Effect.fail(new ApiProblem({ operation: "session", status: 401, title: "Sign in required" }))
    return mount({ overview: unauthorized, providers: Effect.fail(new TransportFailure({ operation: "providers", cause: "offline" })) })
      .then((fallback) =>
        settle(fallback).then(() => {
          expect(fallback.shadowRoot?.querySelector("#sign-in input[type='email']")).not.toBe(null)
          fallback.remove()
          return mount({ overview: unauthorized, providers: Effect.succeed(["google"]) })
        })
      )
      .then((oauth) =>
        settle(oauth).then(() => {
          expect(oauth.shadowRoot?.querySelector("#sign-in form")).toBe(null)
          expect(oauth.shadowRoot?.querySelector("#sign-in a")?.textContent).toContain("Google")
          oauth.remove()
          return mount({ overview: unauthorized, providers: Effect.succeed([]) })
        })
      )
      .then((none) =>
        settle(none).then(() => {
          expect(none.shadowRoot?.querySelector("#sign-in form")).toBe(null)
          expect(none.shadowRoot?.querySelector("#sign-in nav")).toBe(null)
        })
      )
  })

  it("refreshes and revokes an authenticated session", () => {
    const refreshSession = Effect.void
    const logout = Effect.void
    return mount({ refreshSession, logout }).then((element) => {
      const buttons = element.shadowRoot?.querySelectorAll<HTMLButtonElement>("[part~='session-actions'] button")
      buttons?.[0]?.click()
      return settle(element)
        .then(() => {
          expect(element.shadowRoot?.querySelector("[part~='mutation-status']")?.textContent).toContain("refreshed")
          element.shadowRoot?.querySelectorAll<HTMLButtonElement>("[part~='session-actions'] button")[1]?.click()
          return settle(element)
        })
        .then(() => {
          expect(element.shadowRoot?.querySelector("[part~='mutation-status']")?.textContent).toContain("Signed out")
        })
    })
  })

  it("prevents a duplicate session action while the first request is pending", () => {
    let attempts = 0
    const refreshSession = Effect.sync(() => {
      attempts += 1
    }).pipe(Effect.zipRight(Effect.never))
    return mount({ refreshSession }).then((element) => {
      const refresh = element.shadowRoot?.querySelector<HTMLButtonElement>("[part~='session-actions'] button")
      refresh?.click()
      refresh?.click()
      return Promise.resolve().then(() => {
        expect(attempts).toBe(1)
        expect(refresh?.disabled).toBe(true)
      })
    })
  })

  it("keeps a grant idempotency key across failure and changes it after success", () => {
    const commands: Mutation[] = []
    let fail = true
    const api: Partial<AdminApiShape> = {
      overview: Effect.succeed(overview),
      mutate: (command) =>
        Effect.sync(() => {
          commands.push(command)
        }).pipe(Effect.flatMap(() => (fail ? Effect.fail(new ApiProblem({ operation: "grant", status: 409, title: "Conflict" })) : Effect.void)))
    }
    return mount(api)
      .then((element) => navigate(element, "financials"))
      .then((element) => {
        const grant = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#grant-account)")
        expect(grant).toBeInstanceOf(HTMLFormElement)
        if (grant instanceof HTMLFormElement) setForm(grant, { account_id: "account_12345678", amount: "5", unit: "wei", reason: "Correction" })
        grant?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
        confirmChange(element)
        return settle(element)
          .then(() => {
            fail = false
            grant?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
            confirmChange(element)
            return settle(element)
          })
          .then(() => {
            grant?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
            confirmChange(element)
            return settle(element)
          })
          .then(() => {
            expect(commands[0]?.idempotencyKey).toBe(commands[1]?.idempotencyKey)
            expect(commands[2]?.idempotencyKey).not.toBe(commands[1]?.idempotencyKey)
          })
      })
  })

  it("confirms scoped status, cap, principal, and capability changes", () => {
    const commands: Mutation[] = []
    return mount({
      mutate: (command) =>
        Effect.sync(() => {
          commands.push(command)
        })
    }).then((element) => {
      const cases = [
        ["tenants", "form:has(#update-tenant-id)", { tenant_id: "tenant_12345678", reason: "Pause tenant" }],
        ["accounts", "form:has(#update-account-id)", { account_id: "account_12345678", exposure_cap: "80", reason: "Bound risk" }],
        ["principals", "form:has(#update-principal-id)", { principal_id: "principal_12345678", reason: "Remove access" }],
        ["policies", "form:has(#policy-account)", { account_id: "account_12345678", capability: "live", model: "", allowed: "false", reason: "Denied" }]
      ] as const
      return cases
        .reduce<Promise<void>>(
          (previous, [route, selector, values]) =>
            previous
              .then(() => navigate(element, route))
              .then(() => {
                const form = element.shadowRoot?.querySelector<HTMLFormElement>(selector)
                if (form instanceof HTMLFormElement) {
                  setForm(form, values)
                  form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
                  confirmChange(element)
                }
                return settle(element).then(() => undefined)
              }),
          Promise.resolve()
        )
        .then(() => {
          expect(commands.map((command) => command.method)).toEqual(["PATCH", "PATCH", "PATCH", "PUT"])
          expect(commands[0]?.path).toBe("/v1/tenants/tenant_12345678")
          expect(commands[1]?.body).toMatchObject({ exposure_cap: "80" })
          expect(commands[2]?.path).toBe("/v1/principals/principal_12345678")
          expect(commands[3]?.body).toMatchObject({ allowed: false, model: null })
        })
    })
  })

  it("reveals an identity invitation once in memory and reports invalid rate time", () => {
    const issueInvitation = vi.fn(() => Effect.succeed("och_inv_123456789012345678901234567890123456789012"))
    return mount({ issueInvitation })
      .then((element) => navigate(element, "principals"))
      .then((element) => {
        const invitation = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#invite-principal)")
        if (invitation instanceof HTMLFormElement) setForm(invitation, { principal_id: "principal_12345678", source_principal_id: "principal_abcdefgh", reason: "Link verified administrator" })
        invitation?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
        return settle(element)
          .then(() => {
            expect(issueInvitation).toHaveBeenCalledOnce()
            const dialog = element.shadowRoot?.querySelector<HTMLDialogElement>("#invitation-secret")
            expect(dialog?.open).toBe(true)
            expect(dialog?.textContent).toContain("och_inv_")
            expect(dialog?.querySelector(":focus")?.textContent).toContain("I saved it")
            expect(localStorage.length).toBe(0)
            expect(sessionStorage.length).toBe(0)
            expect(location.href).not.toContain("och_inv_")
            dialog?.querySelector<HTMLButtonElement>("button")?.click()
            return settle(element)
          })
          .then(() => {
            expect(element.shadowRoot?.querySelector("#invitation-secret")).toBe(null)
            return navigate(element, "policies")
          })
          .then(() => {
            const rate = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#rate-capability)")
            if (rate instanceof HTMLFormElement) setForm(rate, { capability: "live", effective_at: "invalid" })
            rate?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
            return element.updateComplete
          })
          .then(() => {
            expect(element.shadowRoot?.querySelector("[part~='mutation-status']")?.textContent).toContain("valid effective time")
          })
      })
  })

  it("clears an invitation secret on navigation and disconnect", () => {
    const secret = "och_inv_123456789012345678901234567890123456789012"
    return mount({ issueInvitation: () => Effect.succeed(secret) })
      .then((element) => navigate(element, "principals"))
      .then((element) => {
        let invitation = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#invite-principal)")
        if (invitation instanceof HTMLFormElement) setForm(invitation, { principal_id: "principal_12345678", source_principal_id: "principal_abcdefgh", reason: "Link verified administrator" })
        invitation?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
        return settle(element)
          .then(() => {
            expect(element.shadowRoot?.textContent).toContain(secret)
            return navigate(element, "accounts")
          })
          .then(() => {
            expect(element.shadowRoot?.textContent).not.toContain(secret)
            return navigate(element, "principals")
          })
          .then(() => {
            invitation = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#invite-principal)")
            if (invitation !== null && invitation !== undefined) setForm(invitation, { principal_id: "principal_12345678", source_principal_id: "principal_abcdefgh", reason: "Link verified administrator" })
            invitation?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
            return settle(element)
          })
          .then(() => {
            expect(element.shadowRoot?.textContent).toContain(secret)
            element.remove()
            return element.updateComplete
          })
          .then(() => {
            expect(element.shadowRoot?.textContent).not.toContain(secret)
          })
      })
  })

  it("keeps invitation failures typed and prevents duplicate in-flight submits", () => {
    const mutate = vi.fn(() => Effect.void)
    const issueInvitation = () => Effect.fail(new ApiProblem({ operation: "invite", status: 409, title: "Conflict" }))
    return mount({ mutate, issueInvitation })
      .then((element) => navigate(element, "tenants"))
      .then((element) => {
        const tenant = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#tenant-name)")
        if (tenant instanceof HTMLFormElement) setForm(tenant, { display_name: "Tenant" })
        tenant?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
        tenant?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
        return settle(element)
          .then(() => {
            expect(mutate).toHaveBeenCalledOnce()
            return navigate(element, "principals")
          })
          .then(() => {
            const invitation = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#invite-principal)")
            if (invitation instanceof HTMLFormElement) setForm(invitation, { principal_id: "principal_12345678", source_principal_id: "principal_abcdefgh", reason: "Retry" })
            invitation?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
            return settle(element)
          })
          .then(() => {
            expect(element.shadowRoot?.querySelector("[part~='mutation-status']")?.textContent).toContain("Conflict (409)")
          })
      })
  })

  it("cancels a proposed financial change without calling the API", () => {
    const mutate = vi.fn(() => Effect.void)
    return mount({ mutate })
      .then((element) => navigate(element, "financials"))
      .then((element) => {
        const grant = element.shadowRoot?.querySelector<HTMLFormElement>("form:has(#grant-account)")
        if (grant instanceof HTMLFormElement) setForm(grant, { account_id: "account_12345678", amount: "1", unit: "wei", reason: "Test" })
        grant?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
        return element.updateComplete.then(() => {
          const dialog = element.shadowRoot?.querySelector<HTMLDialogElement>("#change-confirmation")
          expect(dialog?.textContent).toContain("Post an immutable ledger grant")
          dialog?.querySelector<HTMLButtonElement>("button[value='cancel']")?.click()
          expect(dialog?.open).toBe(false)
          expect(mutate).not.toHaveBeenCalled()
        })
      })
  })

  it("requires an explicit reason before changing the global control", () => {
    const commands: Mutation[] = []
    return mount({
      overview: Effect.succeed(overview),
      mutate: (command) =>
        Effect.sync(() => {
          commands.push(command)
        })
    })
      .then((element) => navigate(element, "operations"))
      .then((element) => {
        element.shadowRoot?.querySelector<HTMLButtonElement>("#emergency button")?.click()
        const dialog = element.shadowRoot?.querySelector<HTMLDialogElement>("dialog")
        expect(dialog?.open).toBe(true)
        expect(dialog?.querySelector(":focus")?.getAttribute("value")).toBe("cancel")
        const reason = dialog?.querySelector<HTMLTextAreaElement>("textarea")
        if (reason !== null && reason !== undefined) reason.value = "Incident containment"
        dialog?.querySelector("form")?.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }))
        return settle(element).then(() => {
          expect(commands[0]?.path).toBe("/v1/operations/kill-switch")
          expect(commands[0]?.body).toEqual({ enabled: true, reason: "Incident containment" })
        })
      })
  })

  it("renders the resume action and closes its native dialog without mutation", () => {
    const stopped = { ...overview, killSwitch: overview.killSwitch === null ? null : { ...overview.killSwitch, enabled: true } }
    const mutate = vi.fn(() => Effect.void)
    return mount({ overview: Effect.succeed(stopped), mutate })
      .then((element) => navigate(element, "operations"))
      .then((element) => {
        const button = element.shadowRoot?.querySelector<HTMLButtonElement>("#emergency button")
        expect(button?.textContent).toContain("Resume")
        button?.click()
        element.shadowRoot?.querySelector<HTMLButtonElement>("dialog button[value='cancel']")?.click()
        expect(element.shadowRoot?.querySelector<HTMLDialogElement>("dialog")?.open).toBe(false)
        expect(mutate).not.toHaveBeenCalled()
      })
  })

  it("uses logical responsive structure and explicit accessibility media rules", () =>
    mount({}).then((element) => {
      element.dir = "rtl"
      return element.updateComplete
        .then(() => {
          expect(getComputedStyle(element).direction).toBe("rtl")
          expect(element.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth)
          return fetch("/src/global.css").then((response) => response.text())
        })
        .then((stylesheet) => {
          expect(stylesheet).toContain("forced-colors: active")
          expect(stylesheet).not.toContain("margin-left")
          expect(stylesheet).not.toContain("margin-right")
        })
    }))
})
