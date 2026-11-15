import type { Status } from "./types";

/**
 * Display severity, worst first. `unknown` ranks worse than `healthy` on purpose:
 * one service that has never been checked keeps the pet in its waiting look,
 * because "healthy" would claim that every service has been checked.
 * This is a display rule only. It is not part of the decision logic in petState.ts.
 */
export const SEVERITY_ORDER: readonly Status[] = ["down", "slow", "recovering", "unknown", "healthy"];

/**
 * The most severe status in the list. An empty list gives "unknown" as a defined fallback.
 * The dashboard never passes an empty list, because validation rejects a response without services.
 */
export function worstStatus(statuses: readonly Status[]): Status {
  for (const status of SEVERITY_ORDER) {
    if (statuses.includes(status)) return status;
  }
  return "unknown";
}
