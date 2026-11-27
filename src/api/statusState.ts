import type { FetchOutcome, PollState, StatusView } from "./types";

/** How many failed requests in a row it takes before the "no signal" overlay appears. */
export const FAILURES_BEFORE_NO_SIGNAL = 2;

export const initialPollState: PollState = {
  data: null,
  receivedAt: null,
  consecutiveFailures: 0,
  lastFailure: null,
};

/**
 * Pure state transition for one finished request. Never mutates `state`.
 * - success: new data, new receive time, failure count back to 0.
 * - failure: count + 1. The last good data and its receive time are kept, so "stale since" does not move.
 * - aborted: nothing changes. An aborted request is not a failure.
 */
export function applyOutcome(state: PollState, outcome: FetchOutcome, nowMs: number): PollState {
  switch (outcome.kind) {
    case "success":
      return { data: outcome.data, receivedAt: nowMs, consecutiveFailures: 0, lastFailure: null };
    case "failure":
      return { ...state, consecutiveFailures: state.consecutiveFailures + 1, lastFailure: outcome.reason };
    case "aborted":
      return state;
  }
}

/** What the UI shows for a state. `no-signal` is a UI state, not a pet status. */
export function toView(state: PollState): StatusView {
  if (state.consecutiveFailures >= FAILURES_BEFORE_NO_SIGNAL) {
    return { kind: "no-signal", data: state.data, staleSinceMs: state.receivedAt };
  }
  if (state.data !== null) return { kind: "live", data: state.data };
  return { kind: "loading" };
}
