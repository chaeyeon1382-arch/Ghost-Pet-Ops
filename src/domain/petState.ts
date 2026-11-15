import type { CheckClass, CheckResult, Decision, Status, Tracker } from "./types";

// Thresholds. Change them here only. See .kiro/specs/mvp/design.md.
export const TIMEOUT_MS = 5000;
export const SLOW_THRESHOLD_MS = 2000; // responseMs <= this is fast
export const SLOW_CHECKS_NEEDED = 2;
export const FAST_CHECKS_NEEDED = 2;
export const FAILURES_TO_DOWN = 3;

export const initialTracker: Tracker = {
  status: "unknown",
  consecutiveFailures: 0,
  consecutiveSlow: 0,
  consecutiveFast: 0,
};

/** Failure: timeout, connection error, or 4xx/5xx. Everything else (including 3xx) is a success. */
export function isFailure(check: CheckResult): boolean {
  return check.timedOut || check.statusCode === null || check.statusCode >= 400;
}

export function classify(check: CheckResult): CheckClass {
  if (isFailure(check)) return "failure";
  return check.responseMs <= SLOW_THRESHOLD_MS ? "fast" : "slow";
}

function updateCounters(prev: Tracker, kind: CheckClass): Omit<Tracker, "status"> {
  return {
    consecutiveFailures: kind === "failure" ? prev.consecutiveFailures + 1 : 0,
    consecutiveSlow: kind === "slow" ? prev.consecutiveSlow + 1 : 0,
    consecutiveFast: kind === "fast" ? prev.consecutiveFast + 1 : 0,
  };
}

/** Pure: same input, same output. No I/O, no clock, no React. */
export function decide(prev: Tracker, check: CheckResult): Decision {
  const kind = classify(check);
  const c = updateCounters(prev, kind);
  const failedEnough = c.consecutiveFailures >= FAILURES_TO_DOWN;
  const slowEnough = c.consecutiveSlow >= SLOW_CHECKS_NEEDED;
  const fastEnough = c.consecutiveFast >= FAST_CHECKS_NEEDED;

  const stay: Decision = { tracker: { status: prev.status, ...c } };
  const move = (to: Status, note: string): Decision => ({
    tracker: { status: to, ...c },
    transition: { from: prev.status, to, note },
  });
  const toDown = () => move("down", `${FAILURES_TO_DOWN} failed checks in a row`);
  const toSlow = (note = "response over 2s twice in a row") => move("slow", note);

  switch (prev.status) {
    case "unknown":
      if (kind === "fast") return move("healthy", "first fast check");
      if (slowEnough) return toSlow();
      if (failedEnough) return toDown();
      return stay;

    case "healthy":
      if (failedEnough) return toDown();
      if (slowEnough) return toSlow();
      return stay;

    case "slow":
      if (failedEnough) return toDown();
      if (fastEnough) return move("healthy", "response at or under 2s twice in a row");
      return stay;

    case "down":
      if (kind === "failure") return stay;
      return move("recovering", "first successful check after outage");

    case "recovering":
      if (kind === "failure") return move("down", "failed again while recovering");
      if (fastEnough) return move("healthy", "two fast checks in a row after outage");
      if (slowEnough) return toSlow("still slow after outage");
      return stay;
  }
}

export interface IndexedTransition {
  index: number;
  from: Status;
  to: Status;
  note: string;
}

/** Fold a sequence of checks into a final tracker plus all transitions, oldest first. */
export function runChecks(
  checks: CheckResult[],
  start: Tracker = initialTracker,
): { tracker: Tracker; transitions: IndexedTransition[] } {
  const transitions: IndexedTransition[] = [];
  let tracker = start;
  checks.forEach((check, index) => {
    const d = decide(tracker, check);
    tracker = d.tracker;
    if (d.transition) transitions.push({ index, ...d.transition });
  });
  return { tracker, transitions };
}
