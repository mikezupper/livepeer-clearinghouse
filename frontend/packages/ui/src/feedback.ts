export type UiFailureReason =
  | "authentication"
  | "access"
  | "conflict"
  | "invalid-response"
  | "unavailable"
  | "rejected"
  | "unknown"

/** Builds safe, actionable UI copy without exposing transport internals. */
export const failureMessage = (task: string, reason: UiFailureReason): string => {
  if (reason === "authentication") return `Your session ended before we could ${task}. Sign in and try again.`
  if (reason === "access") return `You do not have permission to ${task}. Sign in with an authorized account or contact the administrator.`
  if (reason === "conflict") return `The data changed while we were trying to ${task}. We returned you to the first page; try again.`
  if (reason === "invalid-response") return `We could not ${task} because the server returned an incompatible response. Refresh and try again.`
  if (reason === "unavailable") return `We could not ${task} because the service did not respond. Check your connection and try again.`
  if (reason === "rejected") return `We could not ${task}. Review the information you entered and try again.`
  return `We could not ${task}. Try again. If the problem continues, contact the administrator.`
}
