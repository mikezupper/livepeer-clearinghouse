import { existsSync, readFileSync } from "node:fs"
import { generate, manifests } from "./custom-elements.mjs"

const failures = manifests.flatMap((entry) => !existsSync(entry.output)
  ? [`${entry.output}: generated custom-elements manifest is missing`]
  : readFileSync(entry.output, "utf8") === generate(entry)
    ? []
    : [`${entry.output}: generated custom-elements manifest has drifted; run npm run manifest:generate`])

if (failures.length > 0) {
  process.stderr.write(`${failures.join("\n")}\n`)
  process.exitCode = 1
}
