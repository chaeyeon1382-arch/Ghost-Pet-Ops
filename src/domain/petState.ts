import type { CheckResult, Decision, Tracker } from "./types";

// Draft thresholds. Adjust here only.
export const TIMEOUT_MS = 5000;
export const SLOW_THRESHOLD_MS = 2000;
export const SLOW_CHECKS_NEEDED = 2;
export const FAILURES_TO_DOWN = 3;

export const initialTracker: Tracker = {
  state: "healthy",
  slowStreak: 0,
  fastStreak: 0,
  failStreak: 0,
};

export function isFailure(check: CheckResult): boolean {
  return (
    check.timedOut ||
    check.statusCode === null ||
    check.statusCode >= 400
  );
}

/** Pure: same input, same output. No I/O, no clock, no React. */
export function decide(prev: Tracker, check: CheckResult): Decision {
  const failed = isFailure(check);

  const counters = failed
    ? { slowStreak: 0, fastStreak: 0, failStreak: prev.failStreak + 1 }
    : {
        failStreak: 0,
        slowStreak:
          check.responseMs > SLOW_THRESHOLD_MS ? prev.slowStreak + 1 : 0,
        fastStreak:
          check.responseMs <= SLOW_THRESHOLD_MS ? prev.fastStreak + 1 : 0,
      };

  const stay: Decision = { tracker: { state: prev.state, ...counters } };
  const move = (to: Tracker["state"], note: string): Decision => ({
    tracker: { state: to, ...counters },
    transition: { from: prev.state, to, note },
  });

  if (failed) {
    if (prev.state === "recovering") {
      return move("down", "failed again while recovering");
    }
    if (prev.state !== "down" && counters.failStreak >= FAILURES_TO_DOWN) {
      return move("down", `${FAILURES_TO_DOWN} failed checks in a row`);
    }
    return stay;
  }

  switch (prev.state) {
    case "down":
      return move("recovering", "first successful check after outage");
    case "recovering":
      return move("healthy", "second successful check after outage");
    case "healthy":
      return counters.slowStreak >= SLOW_CHECKS_NEEDED
        ? move("slow", "response over 2s twice in a row")
        : stay;
    case "slow":
      return counters.fastStreak >= SLOW_CHECKS_NEEDED
        ? move("healthy", "response at or under 2s twice in a row")
        : stay;
  }
}

/** Fold a sequence of checks into a final tracker plus all transitions. */
export function runChecks(checks: CheckResult[], start: Tracker = initialTracker) {
  const transitions: { index: number; from: Tracker["state"]; to: Tracker["state"]; note: string }[] = [];
  let tracker = start;
  checks.forEach((check, index) => {
    const d = decide(tracker, check);
    tracker = d.tracker;
    if (d.transition) transitions.push({ index, ...d.transition });
  });
  return { tracker, transitions };
}
