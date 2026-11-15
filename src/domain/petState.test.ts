import { describe, expect, it } from "vitest";
import { decide, initialTracker, isFailure, runChecks } from "./petState";
import type { CheckResult, Tracker } from "./types";

const ok = (responseMs: number): CheckResult => ({ statusCode: 200, responseMs, timedOut: false });
const timeout: CheckResult = { statusCode: null, responseMs: 5000, timedOut: true };
const err500: CheckResult = { statusCode: 500, responseMs: 120, timedOut: false };
const down: Tracker = { ...initialTracker, state: "down", failStreak: 3 };

describe("isFailure", () => {
  it("treats timeouts, connection errors, 4xx and 5xx as failures", () => {
    expect(isFailure(timeout)).toBe(true);
    expect(isFailure({ statusCode: null, responseMs: 10, timedOut: false })).toBe(true);
    expect(isFailure({ statusCode: 404, responseMs: 10, timedOut: false })).toBe(true);
    expect(isFailure(err500)).toBe(true);
  });
  it("treats 2xx and 3xx as success", () => {
    expect(isFailure(ok(100))).toBe(false);
    expect(isFailure({ statusCode: 302, responseMs: 100, timedOut: false })).toBe(false);
  });
});

describe("scenarios", () => {
  it("1. 200 at 150 ms -> healthy", () => {
    expect(runChecks([ok(150)]).tracker.state).toBe("healthy");
  });
  it("2. one 2300 ms check -> stays healthy", () => {
    expect(runChecks([ok(2300)]).tracker.state).toBe("healthy");
  });
  it("3. two 2300 ms checks in a row -> slow", () => {
    const r = runChecks([ok(2300), ok(2300)]);
    expect(r.tracker.state).toBe("slow");
    expect(r.transitions[0]).toMatchObject({ from: "healthy", to: "slow" });
  });
  it("4. one timeout -> state unchanged", () => {
    expect(decide(initialTracker, timeout).tracker.state).toBe("healthy");
    expect(decide(initialTracker, timeout).transition).toBeUndefined();
  });
  it("5. three timeouts or 5xx in a row -> down", () => {
    expect(runChecks([timeout, timeout, timeout]).tracker.state).toBe("down");
    expect(runChecks([err500, err500, err500]).tracker.state).toBe("down");
    expect(runChecks([timeout, err500, timeout]).tracker.state).toBe("down");
  });
  it("6. down, then one success -> recovering", () => {
    expect(decide(down, ok(100)).tracker.state).toBe("recovering");
  });
  it("7. recovering, then one more success -> healthy", () => {
    const r = runChecks([ok(100), ok(100)], down);
    expect(r.tracker.state).toBe("healthy");
  });
});

describe("edge cases", () => {
  it("two failures keep the current state", () => {
    expect(runChecks([timeout, timeout]).tracker.state).toBe("healthy");
  });
  it("a success between failures resets the failure count", () => {
    expect(runChecks([timeout, timeout, ok(100), timeout, timeout]).tracker.state).toBe("healthy");
  });
  it("a fast check between slow checks resets the slow streak", () => {
    expect(runChecks([ok(2300), ok(100), ok(2300)]).tracker.state).toBe("healthy");
  });
  it("a failure between slow checks resets the slow streak", () => {
    expect(runChecks([ok(2300), timeout, ok(2300)]).tracker.state).toBe("healthy");
  });
  it("slow -> healthy needs two fast checks", () => {
    const slow: Tracker = { ...initialTracker, state: "slow" };
    expect(runChecks([ok(500)], slow).tracker.state).toBe("slow");
    expect(runChecks([ok(500), ok(500)], slow).tracker.state).toBe("healthy");
  });
  it("a slow state goes down after three failures", () => {
    const slow: Tracker = { ...initialTracker, state: "slow" };
    expect(runChecks([timeout, timeout, timeout], slow).tracker.state).toBe("down");
  });
  it("a failure while recovering returns to down immediately", () => {
    const rec: Tracker = { ...initialTracker, state: "recovering" };
    const d = decide(rec, timeout);
    expect(d.tracker.state).toBe("down");
    expect(d.transition?.to).toBe("down");
  });
  it("down stays down on further failures with no new transition", () => {
    expect(decide(down, timeout).transition).toBeUndefined();
  });
  it("exactly 2000 ms counts as fast", () => {
    expect(runChecks([ok(2000), ok(2000)]).tracker.state).toBe("healthy");
  });
  it("does not mutate the input tracker", () => {
    const before = { ...initialTracker };
    decide(initialTracker, ok(3000));
    expect(initialTracker).toEqual(before);
  });
});
