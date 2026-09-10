import { expect, test, type Page } from "@playwright/test"

const required = (name: string): string => {
  const value = process.env[name]
  if (value === undefined || value === "") throw new Error(`Missing ${name}`)
  return value
}

const edge = required("QUALIFICATION_EDGE_URL")
const mailbox = required("QUALIFICATION_MAILBOX_URL")
const mailboxToken = required("QUALIFICATION_MAILBOX_TOKEN")

const latestCode = async (email: string): Promise<string> => {
  const response = await fetch(`${mailbox}/_qualification/latest?${new URLSearchParams({ email })}`, {
    headers: { Authorization: `Bearer ${mailboxToken}` }
  })
  if (!response.ok) throw new Error("Protected qualification mailbox was unavailable")
  const payload: unknown = await response.json()
  if (typeof payload !== "object" || payload === null || !("code" in payload) || typeof payload.code !== "string") {
    throw new Error("Protected qualification mailbox returned an invalid response")
  }
  return payload.code
}

const signInUser = async (page: Page, email: string): Promise<void> => {
  await page.getByLabel("Email address").first().fill(email)
  await page.getByRole("button", { name: "Send one-time code" }).click()
  await expect(page.getByText("one-time code has been sent", { exact: false })).toBeVisible()
  const code = await latestCode(email)
  await page.getByLabel("Email address").last().fill(email)
  await page.getByLabel("Six-digit code").fill(code)
  await page.getByRole("button", { name: /Verify and sign in/ }).click()
}

const signInAdmin = async (page: Page, email: string): Promise<void> => {
  await page.getByLabel("Email address").fill(email)
  await page.getByRole("button", { name: "Email a one-time code" }).click()
  await expect(page.getByText("six-digit code was requested", { exact: false })).toBeVisible()
  await page.getByLabel("One-time code").fill(await latestCode(email))
  await page.getByRole("button", { name: "Verify code" }).click()
}

test("production admin and user applications render the authoritative settlement", async ({ browser }) => {
  const accountName = required("QUALIFICATION_ACCOUNT_NAME")
  const chargeId = required("QUALIFICATION_CHARGE_ID")
  const adminContext = await browser.newContext()
  const admin = await adminContext.newPage()
  const adminApiResponses: number[] = []
  admin.on("response", (response) => {
    if (response.url().startsWith(`${edge}/v1/`)) adminApiResponses.push(response.status())
  })
  await admin.goto(`${edge}/admin/`)
  await signInAdmin(admin, required("QUALIFICATION_OPERATOR_EMAIL"))
  await expect(admin.getByRole("heading", { name: "Authoritative overview" })).toBeVisible()
  await expect(admin.getByText(accountName)).toBeVisible()
  await expect(admin.getByText(chargeId)).toBeVisible()
  expect(adminApiResponses.some((status) => status === 200)).toBe(true)
  await adminContext.close()

  const userContext = await browser.newContext()
  const user = await userContext.newPage()
  const userApiResponses: number[] = []
  user.on("response", (response) => {
    if (response.url().startsWith(`${edge}/v1/`)) userApiResponses.push(response.status())
  })
  await user.goto(`${edge}/`)
  await signInUser(user, required("QUALIFICATION_USER_EMAIL"))
  await expect(user.getByRole("heading", { name: accountName })).toBeVisible()
  await user.getByRole("link", { name: "Charges" }).click()
  await expect(user.getByRole("heading", { name: "Charges" })).toBeVisible()
  await expect(user.getByText(chargeId)).toBeVisible()
  await user.getByRole("link", { name: "Usage" }).click()
  await expect(user.getByRole("heading", { name: "Usage" })).toBeVisible()
  await expect(user.getByText("1 fixed")).toBeVisible()
  expect(userApiResponses.some((status) => status === 200)).toBe(true)
  await userContext.close()
})
