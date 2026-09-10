import AxeBuilder from "@axe-core/playwright"
import { expect, test } from "@playwright/test"
import { appUrl, installMockApi } from "./mock-api.js"
import { expectSemanticPage } from "./semantic.js"

for (const application of ["admin", "user"] as const) {
  test(`${application} application has semantic structure and no serious accessibility violations`, async ({ page }) => {
    await installMockApi(page, application)
    await page.goto(appUrl[application])
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
    await expectSemanticPage(page)
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()
    expect(result.violations).toEqual([])
  })
}
