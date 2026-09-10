import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: ".",
  testMatch: "clearinghouse.live.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["line"]],
  use: { ...devices["Desktop Chrome"], screenshot: "off", trace: "off", video: "off" }
})
