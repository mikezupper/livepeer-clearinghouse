import { expect, type Page } from "@playwright/test"

export const expectSemanticPage = (page: Page): Promise<void> => page.evaluate(() => {
  const roots: Array<Document | ShadowRoot> = [document]
  for (let index = 0; index < roots.length; index += 1) {
    for (const element of roots[index]?.querySelectorAll("*") ?? []) if (element.shadowRoot !== null) roots.push(element.shadowRoot)
  }
  const all = (selector: string) => roots.flatMap((root) => [...root.querySelectorAll(selector)])
  const failures: string[] = []
  if (all("main").length !== 1) failures.push("page must expose exactly one main landmark")
  if (all("h1").length !== 1) failures.push("page must expose exactly one h1")
  if (all("[style]").length !== 0) failures.push("inline style attributes are prohibited")
  for (const table of all("table")) {
    if (table.querySelector(":scope > caption") === null) failures.push("every table requires a caption")
    if ([...table.querySelectorAll("th")].some((heading) => !heading.hasAttribute("scope"))) failures.push("every table heading requires scope")
  }
  for (const control of all("input, select, textarea")) {
    const id = control.getAttribute("id")
    const root = control.getRootNode() as Document | ShadowRoot
    if (id === null || root.querySelector(`label[for="${CSS.escape(id)}"]`) === null) failures.push("every form control requires an explicit label")
  }
  return failures
}).then((failures) => { expect(failures).toEqual([]) })
