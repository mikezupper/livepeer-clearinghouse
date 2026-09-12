import type { ReactiveController, ReactiveControllerHost } from "lit"

export type CursorDirection = "next" | "previous"

export class CursorPaginationController implements ReactiveController {
  readonly #host: ReactiveControllerHost
  readonly #parameter: string
  #previous: Array<string | null> = []
  #next: string | null = null

  constructor(host: ReactiveControllerHost, parameter = "cursor") {
    this.#host = host
    this.#parameter = parameter
    host.addController(this)
  }

  hostConnected(): void { this.#host.requestUpdate() }
  get cursor(): string | null { return new URL(location.href).searchParams.get(this.#parameter) }
  get hasPrevious(): boolean { return this.#previous.length > 0 }
  get hasNext(): boolean { return this.#next !== null }
  get pageNumber(): number { return this.#previous.length + 1 }

  received(next: string | null): void {
    this.#next = next
    this.#host.requestUpdate()
  }

  move(direction: CursorDirection): boolean {
    const current = this.cursor
    let target: string | null
    if (direction === "next") {
      if (this.#next === null) return false
      this.#previous = [...this.#previous, current]
      target = this.#next
    } else {
      const prior = this.#previous.at(-1)
      if (prior === undefined) return false
      this.#previous = this.#previous.slice(0, -1)
      target = prior
    }
    const url = new URL(location.href)
    if (target === null) url.searchParams.delete(this.#parameter)
    else url.searchParams.set(this.#parameter, target)
    history.pushState({ ochCursorPrevious: this.#previous }, "", url)
    this.#next = null
    this.#host.requestUpdate()
    return true
  }

  reset(): void {
    this.#previous = []
    this.#next = null
    const url = new URL(location.href)
    url.searchParams.delete(this.#parameter)
    history.replaceState({ ochCursorPrevious: [] }, "", url)
    this.#host.requestUpdate()
  }

  restore(): void {
    const state: unknown = history.state
    const previous = state !== null && typeof state === "object"
      ? Reflect.get(state, "ochCursorPrevious") : undefined
    this.#previous = Array.isArray(previous) && previous.every((value) =>
      value === null || typeof value === "string") ? [...previous] : []
    this.#next = null
    this.#host.requestUpdate()
  }
}
