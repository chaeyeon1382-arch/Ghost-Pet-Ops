import { runChecks } from "../domain/petState";
import type { CheckResult, PetState } from "../domain/types";
import type { StatusResponse } from "./types";

const ok = (responseMs: number): CheckResult => ({ statusCode: 200, responseMs, timedOut: false });
const timeout: CheckResult = { statusCode: null, responseMs: 5000, timedOut: true };
const unavailable: CheckResult = { statusCode: 503, responseMs: 90, timedOut: false };

/**
 * Scripted check sequences for the demo buttons. Each one runs through the
 * real decision logic, so the resulting state and events are not hard-coded.
 */
export const DEMO_SCENARIOS: Record<PetState, CheckResult[]> = {
  healthy: [ok(84)],
  slow: [ok(2300), ok(2450)],
  down: [timeout, unavailable, timeout],
  recovering: [timeout, unavailable, timeout, ok(140)],
};

const CHECK_INTERVAL_MS = 60_000;

/** Builds a response in the API format. `now` is the time of the last check. */
export function buildMockResponse(
  scenario: PetState,
  now: Date = new Date(),
): StatusResponse {
  const checks = DEMO_SCENARIOS[scenario];
  const { tracker, transitions } = runChecks(checks);
  const timeOf = (i: number) =>
    new Date(now.getTime() - (checks.length - 1 - i) * CHECK_INTERVAL_MS).toISOString();
  const last = checks[checks.length - 1];

  return {
    services: [
      {
        id: "site-1",
        name: "Example",
        status: tracker.state,
        responseMs: last.responseMs,
        lastChecked: timeOf(checks.length - 1),
      },
    ],
    events: transitions
      .map((t) => ({ serviceId: "site-1", from: t.from, to: t.to, at: timeOf(t.index), note: t.note }))
      .reverse(), // newest first
  };
}
