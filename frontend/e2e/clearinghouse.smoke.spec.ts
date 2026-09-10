import { expect, test } from "@playwright/test"
import { appUrl, installMockApi } from "./mock-api.js"

for (const application of ["admin", "user"] as const) {
  test(`${application} application loads in the secondary browser engine`, async ({ page }) => {
    await installMockApi(page, application)
    await page.goto(appUrl[application])
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
    await expect(page.locator(application === "admin" ? "admin-app" : "user-app")).toBeVisible()
  })
}
