import type { FetchOutcome } from "./types";
import { validateStatus } from "./validateStatus";

/** Relative on purpose: it works behind the same origin (state-store-read-api design 6.3, option B). */
export const STATUS_PATH = "/status";

const networkFailure: FetchOutcome = { kind: "failure", reason: { kind: "network" }, retryAfter: null };
const aborted: FetchOutcome = { kind: "aborted" };

/**
 * One GET /status request. Never rejects: every problem is returned as a FetchOutcome.
 * - A network error is a failure, unless the signal was aborted. Then it is `aborted`, which is not a failure.
 * - A non-2xx status is a failure. The body is never read, because API Gateway and CloudFront
 *   can return their own bodies (a 429 does not use the api-format.md error format).
 * - A body that is not JSON, or does not match api-format.md, is a failure.
 * `fetchImpl` is a parameter so that tests never touch the network.
 */
export async function fetchStatus(
  signal: AbortSignal,
  fetchImpl: typeof fetch = (input, init) => fetch(input, init),
): Promise<FetchOutcome> {
  let res: Response;
  try {
    res = await fetchImpl(STATUS_PATH, { signal, headers: { Accept: "application/json" } });
  } catch {
    return signal.aborted ? aborted : networkFailure;
  }

  if (!res.ok) {
    return {
      kind: "failure",
      reason: { kind: "http", status: res.status },
      retryAfter: res.headers.get("Retry-After"),
    };
  }

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return signal.aborted ? aborted : { kind: "failure", reason: { kind: "invalid_json" }, retryAfter: null };
  }

  const result = validateStatus(body);
  if (!result.ok) {
    return { kind: "failure", reason: { kind: "invalid_shape", field: result.field }, retryAfter: null };
  }
  return { kind: "success", data: result.data };
}
