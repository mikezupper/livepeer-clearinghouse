import { readdirSync, readFileSync, statSync } from "node:fs"
import { dirname, join, relative, resolve, sep } from "node:path"

const roots = ["apps", "packages"]
const sourceFiles = roots.flatMap(function walk(root) {
  return readdirSync(root).flatMap((entry) => {
    const path = join(root, entry)
    return statSync(path).isDirectory()
      ? walk(path)
      : path.includes(`${sep}src${sep}`) && path.endsWith(".ts") && !path.endsWith(".test.ts")
        ? [path]
        : []
  })
})

const forbidden = [
  [/\b(?:async|await)\b/u, "async/await"],
  [/\bthrow\b/u, "throw"],
  [/\btry\s*\{/u, "try/catch"],
  [/\bstyleMap\b/u, "styleMap"],
  [/\b(?:unsafeHTML|unsafeSVG|unsafeCSS|unsafeStatic)\b/u, "unsafe template API"],
  [/\b(?:innerHTML|insertAdjacentHTML)\b/u, "raw HTML sink"],
  [/\bstyle\s*=/u, "inline style attribute"]
]

const violations = sourceFiles.flatMap((path) => {
  const source = readFileSync(path, "utf8")
  const syntaxViolations = forbidden.flatMap(([pattern, label]) => pattern.test(source) ? [`${path}: ${label}`] : [])
  const importViolations = [...source.matchAll(/\b(?:from\s+|import\s*)["']([^"']+)["']/gu)].flatMap((match) => {
    const specifier = match[1]
    if (specifier === undefined) return []
    if (specifier.startsWith(".")) {
      const target = relative(process.cwd(), resolve(dirname(path), specifier)).split(sep).join("/")
      const sourceArea = path.split("/").slice(0, 2).join("/")
      return target.startsWith(`${sourceArea}/`) ? [] : [`${path}: import crosses source boundary (${specifier})`]
    }
    const allowed = path.startsWith("packages/contracts/")
      ? ["effect"]
      : path.startsWith("packages/platform/")
        ? ["effect", "@effect/", "@livepeer/clearinghouse-contracts"]
        : path.startsWith("packages/ui/")
          ? ["lit", "lit/", "@lit/"]
          : [
            "effect", "@effect/", "lit", "lit/", "@lit/",
            "@livepeer/clearinghouse-contracts", "@livepeer/clearinghouse-platform",
            "@livepeer/clearinghouse-styles", "@livepeer/clearinghouse-styles/", "@livepeer/clearinghouse-ui"
          ]
    return allowed.some((entry) => entry.endsWith("/") ? specifier.startsWith(entry) : specifier === entry)
      ? []
      : [`${path}: dependency is outside its allowed architecture boundary (${specifier})`]
  })
  return [...syntaxViolations, ...importViolations]
})

if (violations.length > 0) {
  process.stderr.write(`${violations.join("\n")}\n`)
  process.exitCode = 1
}
