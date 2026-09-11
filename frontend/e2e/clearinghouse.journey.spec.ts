import { expect, test } from "@playwright/test"
import { appUrl, ids, installMockApi } from "./mock-api.js"

test("administrator signs in and performs tenant and ledger operations", async ({ page }) => {
  const api = await installMockApi(page, "admin", false)
  await page.goto(appUrl.admin)
  await expect(page.getByRole("heading", { name: "Sign in to administer the clearinghouse" })).toBeVisible()
  await page.getByLabel("Email address").fill("operator@example.test")
  await page.getByRole("button", { name: "Email a one-time code" }).click()
  await page.getByLabel("One-time code").fill("123456")
  await page.getByRole("button", { name: "Verify code" }).click()
  await expect(page.getByRole("heading", { name: "Authoritative overview" })).toBeVisible()
  await page.getByRole("link", { name: "Tenants" }).click()
  await expect(page.getByText("Video team", { exact: true })).toBeVisible()

  const tenantCard = page.locator("article").filter({ hasText: "Create tenant" })
  await tenantCard.getByLabel("Display name").fill("Research tenant")
  await tenantCard.getByRole("button", { name: "Create tenant" }).click()
  await expect(page.getByText("Change saved and authoritative data refreshed.")).toBeVisible()

  await page.getByRole("link", { name: "Financials" }).click()
  const grantCard = page.locator("article").filter({ hasText: "Post ledger grant" })
  await grantCard.getByLabel("Account ID").fill(ids.account)
  await grantCard.getByLabel("Signed amount").fill("250")
  await grantCard.getByLabel("Audit reason").fill("Approved service allocation")
  const grantRequestPromise = page.waitForRequest((request) => request.method() === "POST" && new URL(request.url()).pathname === "/v1/grants")
  await grantCard.getByRole("button", { name: "Post grant" }).click()
  await expect(page.getByRole("dialog", { name: "Confirm permanent change" })).toBeVisible()
  await page.getByRole("button", { name: "Confirm change" }).click()
  const grantRequest = await grantRequestPromise
  await expect(page.getByText("Change saved and authoritative data refreshed.")).toBeVisible()

  expect(api.requests.some((request) => request.method() === "POST" && new URL(request.url()).pathname === "/v1/tenants")).toBe(true)
  expect(grantRequest.postDataJSON()).toMatchObject({ account_id: ids.account, amount: { amount: "250", unit: "wei" } })
})

test("account user signs in and reads authoritative balance, usage, and charge", async ({ page }) => {
  await installMockApi(page, "user", false)
  await page.goto(appUrl.user)
  await page.getByLabel("Email address", { exact: true }).first().fill("member@example.test")
  await page.getByRole("button", { name: "Send one-time code" }).click()
  await page.getByLabel("Email address", { exact: true }).nth(1).fill("member@example.test")
  await page.getByLabel("Six-digit code").fill("123456")
  await page.getByRole("button", { name: "Verify and sign in" }).click()
  await expect(page.getByRole("heading", { name: "Production studio" })).toBeVisible()
  await expect(page.getByText("800 wei", { exact: true })).toBeVisible()
  await page.getByRole("link", { name: "Usage" }).click()
  await expect(page.getByText(ids.usage, { exact: true })).toBeVisible()
  await page.getByRole("link", { name: "Charges" }).click()
  await expect(page.getByText("2 wei", { exact: true })).toBeVisible()
  await expect(page.getByText(ids.usage, { exact: true })).toBeVisible()
})
