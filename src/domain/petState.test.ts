import { describe, expect, it } from "vitest";
import { classify, decide, initialTracker, isFailure, runChecks } from "./petState";
import type { CheckResult, Status, Tracker } from "./types";

const res = (statusCode: number | null, responseMs: number): CheckResult => ({
  statusCode,
  responseMs,
  timedOut: false,
});
const ok = (responseMs: number) => res(200, responseMs);
const fast = ok(150);
const slow = ok(2300);
const timeout: CheckResult = { statusCode: null, responseMs: 5000, timedOut: true };
const err503 = res(503, 90);
const from = (status: Status): Tracker => ({ ...initialTracker, status });
const statusAfter = (checks: CheckResult[], start = initialTracker) =>
  runChecks(checks, start).tracker.status;
/** Status after each check, to assert intermediate steps. */
const path = (checks: CheckResult[], start = initialTracker) =>
  checks.map((_, i) => statusAfter(checks.slice(0, i + 1), start));

describe("check classification", () => {
  it("treats timeout, connection error, 4xx and 5xx as failure", () => {
    expect(isFailure(timeout)).toBe(true);
    expect(isFailure(res(null, 30))).toBe(true);
    expect(isFailure(res(404, 30))).toBe(true);
    expect(isFailure(res(400, 30))).toBe(true);
    expect(isFailure(err503)).toBe(true);
    expect(isFailure(res(599, 30))).toBe(true);
  });
  it("16. treats 3xx as success", () => {
    expect(classify(res(301, 100))).toBe("fast");
    expect(classify(res(304, 100))).toBe("fast");
    expect(classify(res(302, 2500))).toBe("slow");
    expect(statusAfter([res(301, 100)])).toBe("healthy");
  });
  it("8. 2000 ms is fast, 2001 ms is slow", () => {
    expect(classify(ok(2000))).toBe("fast");
    expect(classify(ok(2001))).toBe("slow");
    expect(statusAfter([ok(2000), ok(2000)], from("healthy"))).toBe("healthy");
    expect(statusAfter([ok(2001), ok(2001)], from("healthy"))).toBe("slow");
  });
});

describe("counters", () => {
  it("failure increments failures and resets slow and fast", () => {
    const t = decide({ ...from("healthy"), consecutiveSlow: 1, consecutiveFast: 4 }, timeout).tracker;
    expect(t).toEqual({ status: "healthy", consecutiveFailures: 1, consecutiveSlow: 0, consecutiveFast: 0 });
  });
  it("fast success increments fast and resets failures and slow", () => {
    const t = decide({ ...from("healthy"), consecutiveFailures: 2, consecutiveSlow: 1 }, fast).tracker;
    expect(t).toEqual({ status: "healthy", consecutiveFailures: 0, consecutiveSlow: 0, consecutiveFast: 1 });
  });
  it("slow success increments slow and resets failures and fast", () => {
    const t = decide({ ...from("healthy"), consecutiveFailures: 2, consecutiveFast: 3 }, slow).tracker;
    expect(t).toEqual({ status: "healthy", consecutiveFailures: 0, consecutiveSlow: 1, consecutiveFast: 0 });
  });
  it("10. consecutiveFailures resets after a success", () => {
    const r = runChecks([timeout, timeout, fast, timeout, timeout], from("healthy"));
    expect(r.tracker.consecutiveFailures).toBe(2);
    expect(r.tracker.status).toBe("healthy");
    expect(r.transitions).toEqual([]);
  });
  it("11. consecutiveSlow resets after a fast success", () => {
    const r = runChecks([slow, fast, slow], from("healthy"));
    expect(r.tracker.consecutiveSlow).toBe(1);
    expect(r.tracker.status).toBe("healthy");
  });
});

describe("agreed scenarios", () => {
  it("1. from unknown: 200 at 150 ms -> healthy", () => {
    expect(statusAfter([fast])).toBe("healthy");
  });
  it("2. from healthy: one 2300 ms check -> stays healthy", () => {
    expect(statusAfter([slow], from("healthy"))).toBe("healthy");
  });
  it("3. from healthy: two 2300 ms checks in a row -> slow", () => {
    const r = runChecks([slow, slow], from("healthy"));
    expect(r.tracker.status).toBe("slow");
    expect(r.transitions).toEqual([
      { index: 1, from: "healthy", to: "slow", note: "response over 2s twice in a row" },
    ]);
  });
  it("4. from healthy: one timeout -> unchanged, no event", () => {
    const d = decide(from("healthy"), timeout);
    expect(d.tracker.status).toBe("healthy");
    expect(d.transition).toBeUndefined();
  });
  it("5. from healthy: three timeouts, 5xx, or a mix -> down", () => {
    expect(path([timeout, timeout, timeout], from("healthy"))).toEqual(["healthy", "healthy", "down"]);
    expect(statusAfter([err503, err503, err503], from("healthy"))).toBe("down");
    expect(statusAfter([timeout, err503, res(null, 10)], from("healthy"))).toBe("down");
  });
  it("6. from down: one success (fast or slow) -> recovering", () => {
    expect(statusAfter([fast], from("down"))).toBe("recovering");
    expect(statusAfter([slow], from("down"))).toBe("recovering");
  });
  it("7. from down: fast, fast -> recovering, then healthy", () => {
    expect(path([fast, fast], from("down"))).toEqual(["recovering", "healthy"]);
  });
  it("9. from recovering: one failure -> down", () => {
    const d = decide({ ...from("recovering"), consecutiveFast: 1 }, timeout);
    expect(d.tracker.status).toBe("down");
    expect(d.transition).toMatchObject({ from: "recovering", to: "down" });
  });
});

describe("unknown", () => {
  it("starts as unknown with zero counters", () => {
    expect(initialTracker).toEqual({
      status: "unknown",
      consecutiveFailures: 0,
      consecutiveSlow: 0,
      consecutiveFast: 0,
    });
    expect(statusAfter([])).toBe("unknown");
  });
  it("12. one fast success -> healthy, with an unknown -> healthy event", () => {
    expect(runChecks([fast]).transitions).toEqual([
      { index: 0, from: "unknown", to: "healthy", note: "first fast check" },
    ]);
  });
  it("13. two slow successes -> slow, stays unknown after the first", () => {
    expect(path([slow, slow])).toEqual(["unknown", "slow"]);
    expect(runChecks([slow, slow]).transitions[0]).toMatchObject({ from: "unknown", to: "slow" });
  });
  it("14. three failures -> down, stays unknown after one or two", () => {
    expect(path([timeout, err503, timeout])).toEqual(["unknown", "unknown", "down"]);
    expect(runChecks([timeout, err503, timeout]).transitions[0]).toMatchObject({ from: "unknown", to: "down" });
  });
  it("a fast success after one slow success -> healthy", () => {
    expect(path([slow, fast])).toEqual(["unknown", "healthy"]);
  });
  it("a fast success after two failures -> healthy", () => {
    expect(statusAfter([timeout, timeout, fast])).toBe("healthy");
  });
});

describe("recovering", () => {
  it("15. from down: fast, slow -> stays recovering with consecutiveFast reset", () => {
    const r = runChecks([fast, slow], from("down"));
    expect(r.tracker.status).toBe("recovering");
    expect(r.tracker.consecutiveFast).toBe(0);
  });
  it("18. from down: slow, slow -> recovering, then slow", () => {
    expect(path([slow, slow], from("down"))).toEqual(["recovering", "slow"]);
    expect(runChecks([slow, slow], from("down")).transitions[1]).toMatchObject({
      from: "recovering",
      to: "slow",
      note: "still slow after outage",
    });
  });
  it("19. from recovering: slow, slow -> slow", () => {
    expect(path([slow, slow], from("recovering"))).toEqual(["recovering", "slow"]);
  });
  it("20. from down: slow, fast, slow -> stays recovering", () => {
    expect(path([slow, fast, slow], from("down"))).toEqual(["recovering", "recovering", "recovering"]);
  });
  it("from down: slow, fast, fast -> healthy", () => {
    expect(path([slow, fast, fast], from("down"))).toEqual(["recovering", "recovering", "healthy"]);
  });
  it("from down: fast, slow, fast -> stays recovering", () => {
    expect(statusAfter([fast, slow, fast], from("down"))).toBe("recovering");
  });
});

describe("other transitions", () => {
  it("slow -> healthy needs two fast successes", () => {
    expect(path([fast, fast], from("slow"))).toEqual(["slow", "healthy"]);
  });
  it("slow: one or two failures keep slow, three -> down", () => {
    expect(path([timeout, timeout, timeout], from("slow"))).toEqual(["slow", "slow", "down"]);
  });
  it("down stays down on failure with no event", () => {
    const d = decide({ ...from("down"), consecutiveFailures: 3 }, timeout);
    expect(d.tracker.status).toBe("down");
    expect(d.tracker.consecutiveFailures).toBe(4);
    expect(d.transition).toBeUndefined();
  });
  it("records only real status changes", () => {
    const r = runChecks([fast, fast, slow, slow, slow, timeout, timeout, timeout, timeout, fast, fast]);
    expect(r.transitions.map((t) => `${t.from}>${t.to}`)).toEqual([
      "unknown>healthy",
      "healthy>slow",
      "slow>down",
      "down>recovering",
      "recovering>healthy",
    ]);
  });
  it("does not mutate the input tracker", () => {
    const start = from("healthy");
    const copy = { ...start };
    decide(start, slow);
    expect(start).toEqual(copy);
  });
});
