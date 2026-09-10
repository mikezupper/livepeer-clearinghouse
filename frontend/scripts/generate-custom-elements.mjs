import { writeFileSync } from "node:fs"
import { generate, manifests } from "./custom-elements.mjs"

for (const entry of manifests) writeFileSync(entry.output, generate(entry))
