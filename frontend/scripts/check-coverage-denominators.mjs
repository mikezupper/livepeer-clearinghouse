import { existsSync, readFileSync, readdirSync, statSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { join, relative, resolve } from "node:path"

export const coverageUnits = [
  {
    name: "admin-web", roots: ["apps/admin-web/src"], reports: ["apps/admin-web/coverage/coverage-final.json"],
    minimum: { lines: 90, statements: 125, functions: 50, branches: 100 }
  },
  {
    name: "user-web", roots: ["apps/user-web/src"], reports: ["apps/user-web/coverage/coverage-final.json"],
    minimum: { lines: 150, statements: 200, functions: 70, branches: 140 }
  },
  {
    name: "contracts", roots: ["packages/contracts/src"], reports: ["packages/contracts/coverage/coverage-final.json"],
    minimum: { lines: 8, statements: 8, functions: 1, branches: 2 }
  },
  {
    name: "platform", roots: ["packages/platform/src"], reports: ["packages/platform/coverage/coverage-final.json"],
    minimum: { lines: 10, statements: 10, functions: 5, branches: 2 }
  },
  {
    name: "ui", roots: ["packages/ui/src"], reports: ["packages/ui/coverage/coverage-final.json"],
    minimum: { lines: 6, statements: 8, functions: 1, branches: 2 }
  }
]

const productionFiles = (root) => readdirSync(root).flatMap(function walk(entry) {
  const path = join(root, entry)
  return statSync(path).isDirectory()
    ? readdirSync(path).flatMap((child) => walk(join(entry, child)))
    : path.endsWith(".ts") && !path.endsWith(".test.ts") ? [resolve(path)] : []
})

export const counter = (values) => ({ total: values.length, covered: values.filter((value) => value > 0).length })
export const metricFailures = (name, metrics, minimum) => Object.entries(metrics).flatMap(([metric, counts]) => {
  const required = minimum[metric]
  const percentage = counts.total === 0 ? 0 : counts.covered / counts.total * 100
  return [
    ...(counts.total < required ? [`${name}: ${metric} denominator ${counts.total} is below required ${required}`] : []),
    ...(percentage < 85 ? [`${name}: ${metric} coverage ${percentage.toFixed(2)}% is below 85% (${counts.covered}/${counts.total})`] : [])
  ]
})

export const coverageFailures = (selected = []) => {
  const unknown = selected.filter((name) => !coverageUnits.some((unit) => unit.name === name))
  if (unknown.length > 0) return unknown.map((name) => `unknown coverage unit: ${name}`)
  const checkedUnits = selected.length === 0 ? coverageUnits : coverageUnits.filter((unit) => selected.includes(unit.name))
  return checkedUnits.flatMap((unit) => {
  const missingReports = unit.reports.filter((path) => !existsSync(path))
  if (missingReports.length > 0) return missingReports.map((path) => `${unit.name}: missing ${path}; run npm run test:coverage first`)
  const report = Object.assign({}, ...unit.reports.map((path) => JSON.parse(readFileSync(path, "utf8"))))
  const reported = new Set(Object.keys(report).map((path) => resolve(path)))
  const missingSources = unit.roots.flatMap(productionFiles).filter((path) => !reported.has(path))
  const values = Object.values(report)
  const metrics = {
    statements: counter(values.flatMap((file) => Object.values(file.s ?? {}))),
    functions: counter(values.flatMap((file) => Object.values(file.f ?? {}))),
    branches: counter(values.flatMap((file) => Object.values(file.b ?? {}).flat())),
    lines: counter(values.flatMap((file) => {
      const byLine = new Map()
      for (const [id, location] of Object.entries(file.statementMap ?? {})) {
        const line = location.start.line
        byLine.set(line, Math.max(byLine.get(line) ?? 0, file.s?.[id] ?? 0))
      }
      return [...byLine.values()]
    }))
  }
  return [
    ...missingSources.map((path) => `${unit.name}: source omitted from coverage: ${relative(process.cwd(), path)}`),
    ...metricFailures(unit.name, metrics, unit.minimum)
  ]
  })
}

if (process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const failures = coverageFailures(process.argv.slice(2))
  if (failures.length > 0) {
    process.stderr.write(`${failures.join("\n")}\n`)
    process.exitCode = 1
  }
}
