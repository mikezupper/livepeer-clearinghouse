import { mkdtempSync, readFileSync, rmSync } from "node:fs"
import { join, relative, resolve } from "node:path"
import { spawnSync } from "node:child_process"

export const manifests = [
  { output: "custom-elements.json", globs: ["apps/admin-web/src/admin-app.ts", "apps/user-web/src/user-app.ts", "packages/ui/src/och-app-shell.ts"] },
  { output: "packages/ui/custom-elements.json", globs: ["packages/ui/src/och-app-shell.ts"] }
]

export const generate = (entry) => {
  const directory = mkdtempSync(resolve(".och-cem-"))
  const outputDirectory = relative(process.cwd(), directory)
  const result = spawnSync(resolve("node_modules/.bin/custom-elements-manifest"), [
    "analyze", "--litelement", "--outdir", outputDirectory, "--globs", ...entry.globs
  ], { cwd: process.cwd(), encoding: "utf8" })
  if (result.status !== 0) {
    rmSync(directory, { recursive: true, force: true })
    process.stderr.write(result.stderr)
    process.exit(result.status ?? 1)
  }
  const content = readFileSync(join(directory, "custom-elements.json"), "utf8")
  rmSync(directory, { recursive: true, force: true })
  return content
}
