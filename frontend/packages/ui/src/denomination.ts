export type DisplayDenomination = "wei" | "eth"

export interface DisplayAmount {
  readonly primary: string
  readonly exactWei: string | undefined
}
type StorageReader = Pick<Storage, "getItem">
type StorageWriter = Pick<Storage, "setItem">

const WEI_PER_ETH = 1_000_000_000_000_000_000n
const MAX_RATE_DECIMAL_PLACES = 24

export const denominationStorageKey = "och.display-denomination"
export const denominationChangeEvent = "och-denomination-change"

export const parseDisplayDenomination = (value: unknown): DisplayDenomination =>
  value === "eth" ? "eth" : "wei"

export const readDisplayDenomination = (
  storage: StorageReader = localStorage
): DisplayDenomination => parseDisplayDenomination(storage.getItem(denominationStorageKey))

export const setDisplayDenomination = (
  value: DisplayDenomination,
  storage: StorageWriter = localStorage,
  target: EventTarget = window
): void => {
  storage.setItem(denominationStorageKey, value)
  target.dispatchEvent(new CustomEvent(denominationChangeEvent, { detail: value }))
}

export const subscribeDisplayDenomination = (
  listener: (value: DisplayDenomination) => void,
  target: Window = window
): (() => void) => {
  const handleChange = (event: Event): void => {
    if (event instanceof CustomEvent) listener(parseDisplayDenomination(event.detail))
  }
  const handleStorage = (event: StorageEvent): void => {
    if (event.key === denominationStorageKey) listener(parseDisplayDenomination(event.newValue))
  }
  target.addEventListener(denominationChangeEvent, handleChange)
  target.addEventListener("storage", handleStorage)
  return () => {
    target.removeEventListener(denominationChangeEvent, handleChange)
    target.removeEventListener("storage", handleStorage)
  }
}

const rationalText = (numerator: bigint, denominator: bigint, unit: string): string =>
  denominator === 1n ? `${numerator} ${unit}` : `${numerator} / ${denominator} ${unit}`

const decimalText = (numerator: bigint, denominator: bigint): { readonly text: string; readonly exact: boolean } => {
  const whole = numerator / denominator
  let remainder = numerator % denominator
  if (remainder === 0n) return { text: whole.toString(), exact: true }

  let digits = ""
  for (let index = 0; index < MAX_RATE_DECIMAL_PLACES && remainder !== 0n; index += 1) {
    remainder *= 10n
    digits += (remainder / denominator).toString()
    remainder %= denominator
  }
  const exact = remainder === 0n
  const fraction = exact ? digits.replace(/0+$/u, "") : digits
  return { text: `${whole}.${fraction}`, exact }
}

export const formatAmount = (
  numerator: string | bigint,
  denominator: string | bigint,
  currency: string,
  denomination: DisplayDenomination
): DisplayAmount => {
  const amountNumerator = BigInt(numerator)
  const amountDenominator = BigInt(denominator)
  if (currency.toLowerCase() !== "wei") {
    return { primary: rationalText(amountNumerator, amountDenominator, currency), exactWei: undefined }
  }
  const exactWei = rationalText(amountNumerator, amountDenominator, "wei")
  if (denomination === "wei") return { primary: exactWei, exactWei: undefined }

  const decimal = decimalText(amountNumerator, amountDenominator * WEI_PER_ETH)
  return {
    primary: `${decimal.exact ? "" : "≈"}${decimal.text} ETH`,
    exactWei
  }
}
