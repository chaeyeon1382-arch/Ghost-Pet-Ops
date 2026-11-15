import { MAX_BACKOFF_MS } from "./config";
import type { FetchOutcome } from "./types";

/**
 * Pure delay calculations for the polling loop. No clock reads and no timers:
 * the caller passes the current time, which is only needed to read a `Retry-After` HTTP date.
 */

// HTTP dates always end in GMT: "Sun, 06 Nov 1994 08:49:37 GMT" or "Sunday, 06-Nov-94 08:49:37 GMT".
// This guard is needed because Date.parse also accepts junk such as "1.5" or "-5" as dates,
// and a zone-less date would be read in the machine's own time zone.
const HTTP_DATE = /^[A-Za-z]{3,9},\s.*\sGMT$/;

/**
 * Reads a `Retry-After` header value. Returns the delay in ms, or null when the value is
 * absent or invalid. Whole seconds ("120") and HTTP dates are accepted. A date in the past gives 0.
 */
export function parseRetryAfter(raw: string | null, nowMs: number): number | null {
  if (raw === null) return null;
  const value = raw.trim();
  if (/^\d+$/.test(value)) return Number(value) * 1000;
  if (HTTP_DATE.test(value)) {
    const at = Date.parse(value);
    if (!Number.isNaN(at)) return Math.max(0, at - nowMs);
  }
  return null;
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

/**
 * How long to wait before the next request.
 * - success: the base interval.
 * - 429 with a valid Retry-After: that delay, kept between the base interval and MAX_BACKOFF_MS.
 * - 429 without one: double the current delay, kept between the same limits.
 * - any other failure: the base interval (design 5.3).
 * - aborted: unchanged.
 */
export function nextDelay(args: {
  outcome: FetchOutcome;
  baseMs: number;
  /** The delay used before this request. */
  currentMs: number;
  nowMs: number;
}): number {
  const { outcome, baseMs, currentMs, nowMs } = args;
  switch (outcome.kind) {
    case "success":
      return baseMs;
    case "aborted":
      return currentMs;
    case "failure": {
      if (outcome.reason.kind !== "http" || outcome.reason.status !== 429) return baseMs;
      const hinted = parseRetryAfter(outcome.retryAfter, nowMs);
      return clamp(hinted ?? currentMs * 2, baseMs, MAX_BACKOFF_MS);
    }
  }
}
