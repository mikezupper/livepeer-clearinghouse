import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"
import { appUrl, installMockApi } from "./mock-api.js"

const applications = ["admin", "user"] as const
const expectedNavigationIcons = { admin: 9, user: 7 } as const

const expectNoDocumentOverflow = async (page: Page): Promise<void> => {
  const dimensions = await page.evaluate(() => {
    const elements = (root: Document | ShadowRoot): readonly Element[] => Array.from(root.querySelectorAll("*")).flatMap((element) => [
      element,
      ...(element.shadowRoot === null ? [] : elements(element.shadowRoot))
    ])
    return {
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      offenders: elements(document)
        .filter((element) => element.getBoundingClientRect().right > document.documentElement.clientWidth)
        .map((element) => ({
          tag: element.tagName.toLowerCase(),
          part: element.getAttribute("part"),
          right: Math.round(element.getBoundingClientRect().right)
        }))
    }
  })
  expect(dimensions.document, `Horizontal overflow: ${JSON.stringify(dimensions.offenders)}`).toBeLessThanOrEqual(dimensions.viewport)
}

for (const application of applications) {
  test(`${application} signed-in console matches its responsive visual baseline`, async ({ page }, testInfo) => {
    const mobile = testInfo.project.name === "chromium-visual-mobile"
    const light = testInfo.project.name === "chromium-visual-desktop-light"
    if (light) {
      await page.addInitScript(() => {
        const applyLightTheme = (): void => {
          document.documentElement.dataset.theme = "light"
        }
        if (document.documentElement === null) {
          const observer = new MutationObserver(() => {
            if (document.documentElement === null) return
            applyLightTheme()
            observer.disconnect()
          })
          observer.observe(document, { childList: true })
        } else applyLightTheme()
      })
    }
    await installMockApi(page, application)
    await page.goto(appUrl[application])
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
    await expect(page.locator(application === "admin" ? "admin-app" : "user-app")).toBeVisible()
    await page.evaluate(() => document.fonts.ready)
    const visibleBrand = page.locator(mobile ? "och-app-shell [part~='mobile-brand']" : "och-app-shell [part~='brand']")
    await expect(visibleBrand).toBeVisible()
    await expect(visibleBrand).toContainText("Livepeer Clearinghouse")
    if (!mobile) {
      const brandColor = await page.locator("och-app-shell [part~='brand-name']").evaluate((element) => getComputedStyle(element).color)
      const sidebarColor = await page.locator("och-app-shell [part~='sidebar']").evaluate((element) => getComputedStyle(element).backgroundColor)
      expect(brandColor).not.toBe(sidebarColor)
    }
    await expect(page.locator("svg[part~='navigation-icon']")).toHaveCount(expectedNavigationIcons[application])
    for (const icon of await page.locator("svg[part~='navigation-icon']").all()) {
      await expect(icon).toHaveAttribute("aria-hidden", "true")
      await expect(icon).toHaveAttribute("focusable", "false")
    }
    expect(await page.evaluate(() => document.documentElement.dataset.theme ?? "dark")).toBe(light ? "light" : "dark")
    await expectNoDocumentOverflow(page)

    const baseline = mobile ? "mobile" : light ? "desktop-light" : "desktop"
    await expect(page).toHaveScreenshot(`${application}-${baseline}.png`, {
      animations: "disabled",
      caret: "hide",
      fullPage: true,
      scale: "css"
    })

    if (mobile) {
      const menu = page.getByRole("button", { name: "Open navigation" })
      const menuGlyphBounds = await page.locator("och-app-shell [part~='menu-icon'] path").evaluate((path) => {
        if (!(path instanceof SVGGraphicsElement)) return { width: 0, height: 0 }
        const bounds = path.getBBox()
        return { width: bounds.width, height: bounds.height }
      })
      expect(menuGlyphBounds.width).toBeGreaterThan(0)
      expect(menuGlyphBounds.height).toBeGreaterThan(0)
      await menu.click()
      await expect(page.locator("och-app-shell [part~='menu-button']")).toHaveAttribute("aria-expanded", "true")
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible()
      const brand = page.locator("och-app-shell [part~='brand']")
      const close = page.locator("och-app-shell [part~='sidebar-close-button']")
      await expect(brand).toBeVisible()
      await expect(brand).toBeInViewport()
      await expect(close).toBeFocused()
      await expect(close).toBeInViewport()
      const sidebarScrollTop = await page.locator("och-app-shell [part~='sidebar']").evaluate((sidebar) => sidebar.scrollTop)
      expect(sidebarScrollTop).toBe(0)
      await expectNoDocumentOverflow(page)
      await expect(page).toHaveScreenshot(`${application}-mobile-navigation.png`, {
        animations: "disabled",
        caret: "hide",
        scale: "css"
      })
    } else {
      const sidebar = page.locator("och-app-shell [part~='sidebar']")
      await expect(sidebar).toBeVisible()
      const sidebarWidth = await sidebar.evaluate((element) => element.getBoundingClientRect().width)
      expect(sidebarWidth).toBeGreaterThanOrEqual(224)
      expect(sidebarWidth).toBeLessThanOrEqual(280)
      const firstNavigationLinkHeight = await page.locator("a[part~='navigation-link']").first().evaluate((element) => element.getBoundingClientRect().height)
      expect(firstNavigationLinkHeight).toBeGreaterThanOrEqual(44)
      expect(firstNavigationLinkHeight).toBeLessThanOrEqual(48)
    }
  })
}
