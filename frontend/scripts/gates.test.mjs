import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { test } from "node:test"
import { coverageUnits, metricFailures } from "./check-coverage-denominators.mjs"
import { generate, manifests } from "./custom-elements.mjs"

test("coverage membership names every independent codebase", () => {
  assert.deepEqual(coverageUnits.map((unit) => unit.name), ["admin-web", "user-web", "contracts", "platform", "ui"])
})

test("every empty or undersized metric fails closed", () => {
  const metrics = {
    lines: { total: 0, covered: 0 }, statements: { total: 1, covered: 1 },
    functions: { total: 0, covered: 0 }, branches: { total: 1, covered: 0 }
  }
  const failures = metricFailures("fixture", metrics, { lines: 1, statements: 2, functions: 1, branches: 2 })
  assert.equal(failures.length, 7)
  assert.match(failures.join("\n"), /lines denominator 0/u)
  assert.match(failures.join("\n"), /functions coverage 0\.00%/u)
  assert.match(failures.join("\n"), /branches coverage 0\.00%/u)
})

test("temporary manifest generation cannot mutate package metadata", () => {
  const before = readFileSync("package.json", "utf8")
  for (const manifest of manifests) assert.equal(generate(manifest), readFileSync(manifest.output, "utf8"))
  assert.equal(readFileSync("package.json", "utf8"), before)
})
