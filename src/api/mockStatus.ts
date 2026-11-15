import { runChecks } from "../domain/petState";
import type { CheckResult, Status } from "../domain/types";
import { MAX_EVENTS, type StatusResponse } from "./types";

const ok = (responseMs: number): CheckResult => ({ statusCode: 200, responseMs, timedOut: false });
const timeout: CheckResult = { statusCode: null, responseMs: 5000, timedOut: true };
const unavailable: CheckResult = { statusCode: 503, responseMs: 90, timedOut: false };

/**
 * Scripted check sequences for the demo buttons. Each one runs through the
 * real decision logic, so the resulting status and events are not hard-coded.
 */
export const DEMO_SCENARIOS: Record<Status, CheckResult[]> = {
  unknown: [],
  healthy: [ok(84)],
  slow: [ok(2300), ok(2450)],
  down: [ok(90), timeout, unavailable, timeout],
  recovering: [ok(90), timeout, unavailable, timeout, ok(2400)],
};

const CHECK_INTERVAL_MS = 60_000;

/** Builds a response in the API format from a list of checks. `now` is the time of the last check. */
export function buildResponseFromChecks(
  checks: CheckResult[],
  now: Date = new Date(),
): StatusResponse {
  const { tracker, transitions } = runChecks(checks);
  const timeOf = (i: number) =>
    new Date(now.getTime() - (checks.length - 1 - i) * CHECK_INTERVAL_MS).toISOString();
  const last = checks.at(-1);

  return {
    services: [
      {
        id: "site-1",
        name: "Example",
        status: tracker.status,
        responseMs: last ? last.responseMs : null,
        lastChecked: last ? timeOf(checks.length - 1) : null,
      },
    ],
    events: transitions
      .map((t) => ({ serviceId: "site-1", from: t.from, to: t.to, at: timeOf(t.index), note: t.note }))
      .reverse() // newest first
      .slice(0, MAX_EVENTS),
  };
}

export function buildMockResponse(scenario: Status, now: Date = new Date()): StatusResponse {
  return buildResponseFromChecks(DEMO_SCENARIOS[scenario], now);
}
