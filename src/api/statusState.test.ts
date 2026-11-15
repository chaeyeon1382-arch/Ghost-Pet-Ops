import { describe, expect, it } from "vitest";
import { FAILURES_BEFORE_NO_SIGNAL, applyOutcome, initialPollState, toView } from "./statusState";
import type { FailureReason, FetchOutcome, PollState, StatusResponse } from "./types";

// Fixed fixtures. Times are plain numbers passed in, never read from the clock.
const T1 = 1_000;
const T2 = 2_000;

const DATA_A: StatusResponse = {
  services: [{ id: "site-1", name: "Example", status: "healthy", responseMs: 84, lastChecked: "2026-11-15T02:00:00Z" }],
  events: [],
};
const DATA_B: StatusResponse = {
  services: [{ id: "site-1", name: "Example", status: "down", responseMs: 5000, lastChecked: "2026-11-15T02:05:00Z" }],
  events: [],
};

const ok = (data: StatusResponse): FetchOutcome => ({ kind: "success", data });
const fail = (reason: FailureReason = { kind: "network" }): FetchOutcome => ({
  kind: "failure",
  reason,
  retryAfter: null,
});
const aborted: FetchOutcome = { kind: "aborted" };

/** Applies outcomes in order, all at time T2 unless a success sets it. */
function run(outcomes: FetchOutcome[], start: PollState = initialPollState): PollState {
  return outcomes.reduce((state, outcome) => applyOutcome(state, outcome, T2), start);
}

describe("threshold", () => {
  it("needs 2 consecutive failures", () => {
    expect(FAILURES_BEFORE_NO_SIGNAL).toBe(2);
  });
});

describe("initial state", () => {
  it("is loading", () => {
    expect(initialPollState).toEqual({ data: null, receivedAt: null, consecutiveFailures: 0, lastFailure: null });
    expect(toView(initialPollState)).toEqual({ kind: "loading" });
  });
});

describe("success", () => {
  it("stores the data and the receive time and shows live", () => {
    const state = applyOutcome(initialPollState, ok(DATA_A), T1);
    expect(state).toEqual({ data: DATA_A, receivedAt: T1, consecutiveFailures: 0, lastFailure: null });
    expect(toView(state)).toEqual({ kind: "live", data: DATA_A });
  });

  it("replaces older data", () => {
    const first = applyOutcome(initialPollState, ok(DATA_A), T1);
    const second = applyOutcome(first, ok(DATA_B), T2);
    expect(second.data).toBe(DATA_B);
    expect(second.receivedAt).toBe(T2);
  });
});

describe("one failure after good data", () => {
  it("keeps showing the data fully, with no overlay", () => {
    const state = applyOutcome(applyOutcome(initialPollState, ok(DATA_A), T1), fail(), T2);
    expect(state.consecutiveFailures).toBe(1);
    expect(state.data).toBe(DATA_A);
    expect(state.receivedAt).toBe(T1);
    expect(toView(state)).toEqual({ kind: "live", data: DATA_A });
  });
});

describe("two or more failures after good data", () => {
  const good = applyOutcome(initialPollState, ok(DATA_A), T1);

  it("shows no-signal on the 2nd consecutive failure, with the last good data and its time", () => {
    const state = run([fail(), fail()], good);
    expect(state.consecutiveFailures).toBe(2);
    expect(toView(state)).toEqual({ kind: "no-signal", data: DATA_A, staleSinceMs: T1 });
  });

  it("keeps the original stale time while failures continue", () => {
    const state = run([fail(), fail(), fail(), fail()], good);
    expect(state.consecutiveFailures).toBe(4);
    expect(toView(state)).toEqual({ kind: "no-signal", data: DATA_A, staleSinceMs: T1 });
  });
});

describe("recovery", () => {
  const good = applyOutcome(initialPollState, ok(DATA_A), T1);

  it("a success after no-signal returns to live with new data and a zero failure count", () => {
    const stale = run([fail(), fail(), fail()], good);
    const recovered = applyOutcome(stale, ok(DATA_B), T2);
    expect(recovered).toEqual({ data: DATA_B, receivedAt: T2, consecutiveFailures: 0, lastFailure: null });
    expect(toView(recovered)).toEqual({ kind: "live", data: DATA_B });
  });

  it("a success after one failure resets the count, so the next single failure is still live", () => {
    const state = run([fail(), ok(DATA_B), fail()], good);
    expect(state.consecutiveFailures).toBe(1);
    expect(toView(state).kind).toBe("live");
  });

  it("failures that are not consecutive never reach no-signal", () => {
    const state = run([fail(), ok(DATA_A), fail(), ok(DATA_A), fail()], good);
    expect(toView(state).kind).toBe("live");
  });
});

describe("failures before any good data", () => {
  it("stays loading after 1 failure", () => {
    const state = run([fail()]);
    expect(state.consecutiveFailures).toBe(1);
    expect(toView(state)).toEqual({ kind: "loading" });
  });

  it("shows no-signal after 2 failures, with no data and no stale time", () => {
    const state = run([fail(), fail()]);
    expect(toView(state)).toEqual({ kind: "no-signal", data: null, staleSinceMs: null });
  });

  it("recovers to live on the first success", () => {
    const state = run([fail(), fail(), fail()]);
    const recovered = applyOutcome(state, ok(DATA_A), T1);
    expect(toView(recovered)).toEqual({ kind: "live", data: DATA_A });
  });
});

describe("aborted requests", () => {
  it("return the same state object", () => {
    const state = applyOutcome(initialPollState, ok(DATA_A), T1);
    expect(applyOutcome(state, aborted, T2)).toBe(state);
  });

  it("do not count as a failure", () => {
    const state = run([aborted, aborted, aborted]);
    expect(state.consecutiveFailures).toBe(0);
    expect(toView(state)).toEqual({ kind: "loading" });
  });

  it("do not reset the failure count", () => {
    expect(run([fail(), aborted, fail()]).consecutiveFailures).toBe(2);
    expect(toView(run([fail(), aborted, fail()])).kind).toBe("no-signal");
  });
});

describe("lastFailure", () => {
  it("carries the reason of the latest failure", () => {
    expect(run([fail({ kind: "http", status: 429 })]).lastFailure).toEqual({ kind: "http", status: 429 });
    const later = run([fail({ kind: "http", status: 429 }), fail({ kind: "invalid_shape", field: "services" })]);
    expect(later.lastFailure).toEqual({ kind: "invalid_shape", field: "services" });
  });

  it("is cleared by a success", () => {
    expect(run([fail(), ok(DATA_A)]).lastFailure).toBeNull();
  });

  it("is not changed by an aborted request", () => {
    expect(run([fail({ kind: "invalid_json" }), aborted]).lastFailure).toEqual({ kind: "invalid_json" });
  });
});

describe("purity", () => {
  it("does not change the state it is given", () => {
    const state: PollState = Object.freeze({
      data: DATA_A,
      receivedAt: T1,
      consecutiveFailures: 1,
      lastFailure: null,
    });
    const next = applyOutcome(state, fail(), T2);
    expect(next).not.toBe(state);
    expect(state.consecutiveFailures).toBe(1);
    expect(next.consecutiveFailures).toBe(2);
  });
});
