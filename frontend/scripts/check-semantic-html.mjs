import { readFileSync } from "node:fs"

const entrypoints = ["apps/admin-web/index.html", "apps/user-web/index.html"]
const failures = entrypoints.flatMap((path) => {
  const source = readFileSync(path, "utf8")
  const checks = [
    [/<html\s+[^>]*lang="[^"]+"[^>]*dir="(?:ltr|rtl)"/u, "html must declare language and direction"],
    [/<meta\s+name="description"\s+content="[^"]+">/u, "a nonempty description is required"],
    [/<meta\s+name="viewport"\s+content="width=device-width, initial-scale=1">/u, "the standard viewport declaration is required"],
    [/<title>[^<]+<\/title>/u, "a nonempty title is required"],
    [/<(?:admin|user)-app><\/(?:admin|user)-app>/u, "an application landmark host is required"],
    [/^(?![\s\S]*\sstyle\s*=)/u, "inline style attributes are prohibited"]
  ]
  const hosts = source.match(/<(?:admin|user)-app><\/(?:admin|user)-app>/gu) ?? []
  return [
    ...checks.flatMap(([pattern, message]) => pattern.test(source) ? [] : [`${path}: ${message}`]),
    ...(hosts.length === 1 ? [] : [`${path}: expected exactly one application host; found ${hosts.length}`])
  ]
})

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`)
  process.exitCode = 1
}
