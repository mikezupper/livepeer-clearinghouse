import { Either } from "effect"
import { afterEach, describe, expect, it, vi } from "vitest"
import { AdminApiLive, reasonFromFailure } from "./api.js"
import { ApiProblem, InvalidPayload, TransportFailure } from "./domain.js"
import { runAdmin } from "./runtime.js"

afterEach(() => { vi.restoreAllMocks(); document.cookie = "och_csrf=; Max-Age=0; Path=/" })

describe("admin API boundary", () => {
  const session = {
    principal_id: "principal_12345678",
    roles: ["operator"],
    expires_at: "2026-09-09T12:00:00Z"
  }
  const metering = {
    status: "ready", open_cases: 0, quarantined: 0, unresolved: 0,
    global_exposure_cap: "100", global_open_exposure: "0",
    last_checkpoint_at: null, last_heartbeat_at: null
  }
  const killSwitch = {
    enabled: false, reason: "Normal operation",
    changed_at: "2026-09-09T12:00:00Z", actor_id: null
  }
  const adapters = [{
    manifest_version: "1.0",
    name: "reference_distribution",
    version: "0.1.0",
    description: "Built-in Open Clearinghouse reference adapters active in this process.",
    source: "builtin",
    builtin: { selector: "reference_distribution" },
    ports: [{ name: "identity", contract_version: "1.0", capabilities: ["resolve"] }]
  }]
  const responses = new Map<string, unknown>([
    ["/v1/auth/session", session],
    ["/v1/tenants?limit=50", { items: [], page: { next_cursor: null } }],
    ["/v1/accounts?limit=100", { items: [{ id: "account_12345678", tenant_id: "tenant_12345678", display_name: "Production", unit: "wei", exposure_cap: "100", status: "active", created_at: "2026-09-09T12:00:00Z" }], page: { next_cursor: null } }],
    ["/v1/principals", []],
    ["/v1/grants?limit=100", { items: [], page: { next_cursor: null } }],
    ["/v1/leases", { items: [] }],
    ["/v1/usage?limit=100", { items: [], page: { next_cursor: null } }],
    ["/v1/charges?limit=100", { items: [], page: { next_cursor: null } }],
    ["/v1/open-reservations?limit=100", { items: [], page: { next_cursor: null } }],
    ["/v1/rate-cards", []],
    ["/v1/operations/reconciliation?limit=100", { items: [], page: { next_cursor: null } }],
    ["/v1/balances/account_12345678", { account_id: "account_12345678", posted: { amount: "75", unit: "wei" }, open_lease_exposure: { amount: "10", unit: "wei" }, available: { amount: "65", unit: "wei" } }],
    ["/v1/operations/metering-health", metering],
    ["/v1/operations/audit-events?limit=100", { items: [], page: { next_cursor: null } }],
    ["/v1/operations/adapters", adapters],
    ["/v1/operations/kill-switch", killSwitch]
  ])

  const serve = (currentSession: unknown = session) => vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.pathname + input.search : input.url
    const payload = path === "/v1/auth/session" ? currentSession : responses.get(path)
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }))
  })

  it("loads and strictly decodes the complete operator projection", () => {
    const fetcher = serve()
    return runAdmin(AdminApiLive.overview).then((result) => {
      expect(Either.isRight(result)).toBe(true)
      if (Either.isRight(result)) {
        expect(result.right.balances[0]?.available.amount).toBe(65n)
        expect(result.right.metering?.status).toBe("ready")
        expect(result.right.adapters?.[0]?.name).toBe("reference_distribution")
      }
      expect(fetcher).toHaveBeenCalledTimes(16)
    })
  })

  it("loads tenant-admin data without requesting operator-only controls", () => {
    const tenantSession = { ...session, roles: ["tenant_admin"] }
    const fetcher = serve(tenantSession)
    return runAdmin(AdminApiLive.overview).then((result) => {
      expect(Either.isRight(result)).toBe(true)
      if (Either.isRight(result)) {
        expect(result.right.metering).toBe(null)
        expect(result.right.killSwitch).toBe(null)
      }
      const paths = fetcher.mock.calls.map(([input]) => String(input))
      expect(paths).not.toContain("/v1/operations/kill-switch")
    })
  })
  it("sends cookie credentials, CSRF, and idempotency headers on mutations", () => {
    document.cookie = `och_csrf=${"c".repeat(40)}; Path=/`
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }))
    return runAdmin(AdminApiLive.mutate({
      path: "/v1/grants", method: "POST", body: {
        account_id: "account_12345678",
        kind: "credit",
        amount: { amount: "1", unit: "wei" },
        reason: "Test",
        external_reference: null
      }, idempotencyKey: "operation-12345678"
    })).then((result) => {
      expect(Either.isRight(result)).toBe(true)
      const init = fetcher.mock.calls[0]?.[1]
      expect(init?.credentials).toBe("same-origin")
      expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("c".repeat(40))
      expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("operation-12345678")
    })
  })

  it("omits optional idempotency and maps HTTP problems", () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 403 }))
    return runAdmin(AdminApiLive.mutate({ path: "/v1/tenants", method: "POST", body: { display_name: "Test" } })).then((result) => {
      expect(Either.isLeft(result)).toBe(true)
      if (Either.isLeft(result)) expect(reasonFromFailure(result.left)).toBe("Request was not accepted (403)")
      expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).has("Idempotency-Key")).toBe(false)
    })
  })

  it("rejects invalid mutation bodies before transport", () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
    return runAdmin(AdminApiLive.mutate({ path: "/v1/accounts", method: "POST", body: {} })).then((result) => {
      expect(Either.isLeft(result)).toBe(true)
      expect(fetcher).not.toHaveBeenCalled()
    })
  })

  it("reports unauthenticated HTTP responses as sign-in required", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 401 }))
    return runAdmin(AdminApiLive.overview).then((result) => {
      expect(Either.isLeft(result)).toBe(true)
      if (Either.isLeft(result)) expect(reasonFromFailure(result.left)).toBe("Sign in required (401)")
    })
  })

  it("maps all typed failure tracks to stable messages", () => {
    expect(reasonFromFailure(new ApiProblem({ operation: "read", status: 401, title: "Sign in required" }))).toBe("Sign in required (401)")
    expect(reasonFromFailure(new InvalidPayload({ operation: "read", cause: "bad" }))).toContain("incompatible")
    expect(reasonFromFailure(new TransportFailure({ operation: "read", cause: "offline" }))).toContain("unavailable")
  })

  it("rejects sessions without an administration role", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      principal_id: "principal_12345678", roles: ["credential_holder"], expires_at: at
    }), { status: 200 }))
    return runAdmin(AdminApiLive.overview).then((result) => {
      expect(Either.isLeft(result)).toBe(true)
      if (Either.isLeft(result)) expect(reasonFromFailure(result.left)).toBe("Administrator role required (403)")
    })
  })

  it("rejects invalid JSON and unavailable fetches", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("not-json", { status: 200 }))
    return runAdmin(AdminApiLive.overview).then((invalid) => {
      expect(Either.isLeft(invalid)).toBe(true)
      vi.restoreAllMocks()
      vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"))
      return runAdmin(AdminApiLive.overview)
    }).then((offline) => { expect(Either.isLeft(offline)).toBe(true) })
  })

  it("runs email, session, and one-time invitation boundaries", () => {
    document.cookie = `och_csrf=${"s".repeat(40)}; Path=/`
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const path = String(input)
      if (path.endsWith("/providers")) return Promise.resolve(new Response(JSON.stringify({ providers: ["email", "github"] }), { status: 200 }))
      if (path.endsWith("/code")) return Promise.resolve(new Response(null, { status: 202 }))
      if (path.endsWith("/verify") || path.endsWith("/refresh")) {
        return Promise.resolve(new Response(JSON.stringify(session), { status: 200 }))
      }
      if (path.includes("identity-invitations")) return Promise.resolve(new Response(JSON.stringify({
        id: "invitation_12345678",
        source_principal_id: "principal_abcdefgh",
        principal_id: "principal_12345678",
        tenant_id: "tenant_12345678",
        expires_at: "2026-09-09T13:00:00Z",
        created_at: "2026-09-09T12:00:00Z",
        invitation_secret: "och_inv_123456789012345678901234567890123456789012"
      }), { status: 201 }))
      return Promise.resolve(new Response(null, { status: 204 }))
    })
    return Promise.all([
      runAdmin(AdminApiLive.requestEmailCode("admin@example.com")),
      runAdmin(AdminApiLive.providers),
      runAdmin(AdminApiLive.verifyEmailCode("admin@example.com", "123456")),
      runAdmin(AdminApiLive.refreshSession),
      runAdmin(AdminApiLive.logout),
      runAdmin(AdminApiLive.issueInvitation(
        "principal_12345678",
        "principal_abcdefgh",
        "Verified owner"
      ))
    ]).then((results) => {
      expect(results.every((result) => result._tag === "Right")).toBe(true)
      expect(fetcher).toHaveBeenCalledTimes(6)
      const invitation = results[5]
      if (invitation !== undefined && Either.isRight(invitation)) expect(invitation.right).toContain("och_inv_")
    })
  })

  it("rejects malformed authentication and invitation inputs before transport", () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
    return Promise.all([
      runAdmin(AdminApiLive.requestEmailCode("not-an-email")),
      runAdmin(AdminApiLive.verifyEmailCode("admin@example.com", "12")),
      runAdmin(AdminApiLive.issueInvitation("bad-id", "principal_abcdefgh", "Reason"))
    ]).then((results) => {
      expect(results.every((result) => result._tag === "Left")).toBe(true)
      expect(fetcher).not.toHaveBeenCalled()
    })
  })

  it("follows opaque cursors without labeling a partial first page authoritative", () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const path = String(input)
      if (path === "/v1/tenants?limit=50") {
        return Promise.resolve(new Response(JSON.stringify({ items: [], page: { next_cursor: "next_page" } }), { status: 200 }))
      }
      if (path === "/v1/tenants?limit=50&cursor=next_page") {
        return Promise.resolve(new Response(JSON.stringify({ items: [], page: { next_cursor: null } }), { status: 200 }))
      }
      const payload = path === "/v1/auth/session" ? session : responses.get(path)
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }))
    })
    return runAdmin(AdminApiLive.overview).then((result) => {
      expect(Either.isRight(result)).toBe(true)
      expect(fetcher.mock.calls.map(([input]) => String(input))).toContain("/v1/tenants?limit=50&cursor=next_page")
    })
  })
})

const at = "2026-09-09T12:00:00Z"
