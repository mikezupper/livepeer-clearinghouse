import { defineConfig, devices } from "@playwright/test"

const admin = "http://127.0.0.1:4173"
const user = "http://127.0.0.1:4174"

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI === undefined ? 0 : 1,
  reporter: [["line"]],
  use: {
    screenshot: "off",
    trace: "off",
    video: "off"
  },
  projects: [
    { name: "chromium-journeys", testMatch: "**/*.journey.spec.ts", use: { ...devices["Desktop Chrome"] } },
    { name: "chromium-accessibility", testMatch: "**/*.accessibility.spec.ts", use: { ...devices["Desktop Chrome"] } },
    {
      name: "chromium-visual-desktop",
      testMatch: "**/*.visual.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
        deviceScaleFactor: 1,
        colorScheme: "dark",
        reducedMotion: "reduce",
        locale: "en-US",
        timezoneId: "UTC"
      }
    },
    {
      name: "chromium-visual-mobile",
      testMatch: "**/*.visual.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 1,
        colorScheme: "dark",
        reducedMotion: "reduce",
        locale: "en-US",
        timezoneId: "UTC",
        hasTouch: true,
        isMobile: true
      }
    },
    {
      name: "chromium-visual-desktop-light",
      testMatch: "**/*.visual.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
        deviceScaleFactor: 1,
        colorScheme: "light",
        reducedMotion: "reduce",
        locale: "en-US",
        timezoneId: "UTC"
      }
    },
    { name: "firefox-smoke", testMatch: "**/*.smoke.spec.ts", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit-smoke", testMatch: "**/*.smoke.spec.ts", use: { ...devices["Desktop Safari"] } }
  ],
  webServer: [
    { command: "npm run build --workspace @livepeer/clearinghouse-admin-web && npm run preview --workspace @livepeer/clearinghouse-admin-web -- --host 127.0.0.1 --port 4173", url: admin, reuseExistingServer: process.env.CI === undefined },
    { command: "npm run build --workspace @livepeer/clearinghouse-user-web && npm run preview --workspace @livepeer/clearinghouse-user-web -- --host 127.0.0.1 --port 4174", url: user, reuseExistingServer: process.env.CI === undefined }
  ]
})
