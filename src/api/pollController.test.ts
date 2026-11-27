import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PollController, type VisibilitySource } from "./pollController";
import { toView } from "./statusState";
import type { FetchOutcome, PollState, StatusResponse } from "./types";

// No network, no real timers, no clock: `fetchStatus` is a fake that the test resolves by hand,
// `now` is a variable, and time only moves when the test advances the fake timers.
const BASE = 10_000;
const T0 = 1_000;

const DATA: StatusResponse = {
  services: [{ id: "site-1", name: "Example", status: "healthy", responseMs: 84, lastChecked: "2026-11-15T02:00:00Z" }],
  events: [],
};

const ok: FetchOutcome = { kind: "success", data: DATA };
const networkError: FetchOutcome = { kind: "failure", reason: { kind: "network" }, retryAfter: null };
const serverError: FetchOutcome = { kind: "failure", reason: { kind: "http", status: 500 }, retryAfter: null };
const aborted: FetchOutcome = { kind: "aborted" };

function tooMany(retryAfter: string | null = null): FetchOutcome {
  return { kind: "failure", reason: { kind: "http", status: 429 }, retryAfter };
}

/** A visibility source the test controls. Counts active listeners so leaks are visible. */
class FakeVisibility implements VisibilitySource {
  hidden = false;
  activeListeners = 0;
  private listeners = new Set<() => void>();

  isHidden(): boolean {
    return this.hidden;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    this.activeListeners = this.listeners.size;
    return () => {
      this.listeners.delete(listener);
      this.activeListeners = this.listeners.size;
    };
  }

  set(hidden: boolean): void {
    this.hidden = hidden;
    for (const listener of [...this.listeners]) listener();
  }
}

interface Call {
  signal: AbortSignal;
  resolve(outcome: FetchOutcome): void;
}

function setup(options: { hidden?: boolean } = {}) {
  const calls: Call[] = [];
  const changes: PollState[] = [];
  const visibility = new FakeVisibility();
  visibility.hidden = options.hidden ?? false;
  let nowMs = T0;

  const controller = new PollController({
    fetchStatus: (signal) =>
      new Promise<FetchOutcome>((resolve) => {
        calls.push({ signal, resolve });
      }),
    baseIntervalMs: BASE,
    now: () => nowMs,
    visibility,
    onChange: (state) => changes.push(state),
  });

  return {
    controller,
    calls,
    changes,
    visibility,
    setNow: (value: number) => {
      nowMs = value;
    },
    /** Resolves the request with the given index and lets the promise handlers run. */
    async respond(index: number, outcome: FetchOutcome) {
      calls[index].resolve(outcome);
      await vi.advanceTimersByTimeAsync(0);
    },
    async advance(ms: number) {
      await vi.advanceTimersByTimeAsync(ms);
    },
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("PollController: loop", () => {
  it("sends the first request as soon as it starts", () => {
    const t = setup();
    t.controller.start();
    expect(t.calls).toHaveLength(1);
  });

  it("sends the next request one interval after the previous one finished", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, ok);
    await t.advance(BASE - 1);
    expect(t.calls).toHaveLength(1);
    await t.advance(1);
    expect(t.calls).toHaveLength(2);
  });

  it("never has two requests in flight", async () => {
    const t = setup();
    t.controller.start();
    await t.advance(5 * 60_000); // the first request never finishes
    expect(t.calls).toHaveLength(1);
  });

  it("measures the interval from the end of the request, not from its start", async () => {
    const t = setup();
    t.controller.start();
    await t.advance(7_000); // the request takes 7 seconds
    await t.respond(0, ok);
    await t.advance(BASE - 1);
    expect(t.calls).toHaveLength(1);
    await t.advance(1);
    expect(t.calls).toHaveLength(2);
  });

  it("publishes the new state with the data and the receive time", async () => {
    const t = setup();
    t.controller.start();
    t.setNow(5_000);
    await t.respond(0, ok);
    expect(t.changes).toHaveLength(1);
    expect(t.changes[0]).toEqual({ data: DATA, receivedAt: 5_000, consecutiveFailures: 0, lastFailure: null });
    expect(t.controller.getState()).toBe(t.changes[0]);
  });
});

describe("PollController: start and stop are idempotent", () => {
  it("start() twice runs one loop, one request and one listener", async () => {
    const t = setup();
    t.controller.start();
    t.controller.start();
    expect(t.calls).toHaveLength(1);
    expect(t.visibility.activeListeners).toBe(1);

    await t.respond(0, ok);
    await t.advance(BASE);
    expect(t.calls).toHaveLength(2); // not 3: a second loop would have sent another request
    await t.respond(1, ok);
    await t.advance(BASE);
    expect(t.calls).toHaveLength(3);
  });

  it("start() while a request is in flight does not send another", () => {
    const t = setup();
    t.controller.start();
    t.controller.start();
    t.controller.start();
    expect(t.calls).toHaveLength(1);
  });

  it("stop() before start() does not throw", () => {
    const t = setup();
    expect(() => t.controller.stop()).not.toThrow();
    expect(t.calls).toHaveLength(0);
    expect(t.visibility.activeListeners).toBe(0);
  });

  it("stop() twice in a row does not throw", () => {
    const t = setup();
    t.controller.start();
    t.controller.stop();
    expect(() => t.controller.stop()).not.toThrow();
    expect(() => t.controller.stop()).not.toThrow();
    expect(t.visibility.activeListeners).toBe(0);
  });

  it("start() after stop() runs exactly one loop", async () => {
    const t = setup();
    t.controller.start();
    t.controller.stop();
    t.controller.start();
    expect(t.calls).toHaveLength(2); // the first one was aborted, the second is live
    expect(t.calls[0].signal.aborted).toBe(true);
    expect(t.calls[1].signal.aborted).toBe(false);
    expect(t.visibility.activeListeners).toBe(1);

    await t.respond(1, ok);
    await t.advance(BASE);
    expect(t.calls).toHaveLength(3);
    await t.respond(2, ok);
    await t.advance(BASE);
    expect(t.calls).toHaveLength(4); // one request per interval, so there is one loop
  });

  it("start() after stop() ignores the late response of the first run", async () => {
    const t = setup();
    t.controller.start();
    t.controller.stop();
    t.controller.start();
    await t.respond(0, ok); // late response of the aborted first request
    expect(t.changes).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("PollController: stop", () => {
  it("aborts the request in flight and removes the listener", () => {
    const t = setup();
    t.controller.start();
    t.controller.stop();
    expect(t.calls[0].signal.aborted).toBe(true);
    expect(t.visibility.activeListeners).toBe(0);
  });

  it("clears the pending timer, so no further request is sent", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, ok);
    expect(vi.getTimerCount()).toBe(1);
    t.controller.stop();
    expect(vi.getTimerCount()).toBe(0);
    await t.advance(10 * BASE);
    expect(t.calls).toHaveLength(1);
  });

  it("publishes nothing and schedules nothing for a response that arrives afterwards", async () => {
    const t = setup();
    t.controller.start();
    t.controller.stop();
    await t.respond(0, ok);
    expect(t.changes).toHaveLength(0);
    expect(t.controller.getState().data).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    await t.advance(10 * BASE);
    expect(t.calls).toHaveLength(1);
  });

  it("ignores visibility changes afterwards", async () => {
    const t = setup();
    t.controller.start();
    t.controller.stop();
    t.visibility.set(true);
    t.visibility.set(false);
    expect(t.calls).toHaveLength(1);
  });
});

describe("PollController: hidden tab", () => {
  it("aborts the request in flight and does not count it as a failure", async () => {
    const t = setup();
    t.controller.start();
    t.visibility.set(true);
    expect(t.calls[0].signal.aborted).toBe(true);

    await t.respond(0, aborted); // what the real fetchStatus returns after an abort
    expect(t.changes).toHaveLength(0);
    expect(t.controller.getState().consecutiveFailures).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the pending timer and sends nothing while hidden", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, ok);
    expect(vi.getTimerCount()).toBe(1);

    t.visibility.set(true);
    expect(vi.getTimerCount()).toBe(0);
    await t.advance(10 * 60_000);
    expect(t.calls).toHaveLength(1);
  });

  it("requests at once when the tab becomes visible again", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, ok);
    t.visibility.set(true);
    await t.advance(60_000);
    t.visibility.set(false);
    expect(t.calls).toHaveLength(2);
    expect(t.calls[1].signal.aborted).toBe(false);
  });

  it("goes back to the normal interval after resuming", async () => {
    const t = setup();
    t.controller.start();
    t.visibility.set(true);
    t.visibility.set(false);
    await t.respond(1, ok);
    await t.advance(BASE - 1);
    expect(t.calls).toHaveLength(2);
    await t.advance(1);
    expect(t.calls).toHaveLength(3);
  });

  it("a response that arrives for a request aborted by hiding is ignored, even if it succeeded", async () => {
    const t = setup();
    t.controller.start();
    t.visibility.set(true);
    await t.respond(0, ok);
    expect(t.changes).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not start a second request when the tab becomes visible during a request", () => {
    const t = setup();
    t.controller.start();
    t.visibility.set(false); // visible again, but it was never hidden
    t.visibility.set(false);
    expect(t.calls).toHaveLength(1);
  });

  it("ignores repeated visible events after resuming", () => {
    const t = setup();
    t.controller.start();
    t.visibility.set(true);
    t.visibility.set(false);
    t.visibility.set(false);
    expect(t.calls).toHaveLength(2);
  });

  it("does not request on start while hidden, and requests when visible", () => {
    const t = setup({ hidden: true });
    t.controller.start();
    expect(t.calls).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
    t.visibility.set(false);
    expect(t.calls).toHaveLength(1);
  });

  it("stays idle while hidden for a long time", async () => {
    const t = setup({ hidden: true });
    t.controller.start();
    await t.advance(60 * 60_000);
    expect(t.calls).toHaveLength(0);
  });

  it("does not schedule a timer for a response that arrives while hidden", async () => {
    const t = setup();
    t.controller.start();
    t.visibility.set(true);
    t.visibility.set(false); // new request, index 1
    t.visibility.set(true); // hides again: request 1 is aborted too
    await t.respond(1, ok);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("PollController: backoff after 429", () => {
  it("waits 20, 40, then 60 seconds, and stays at 60", async () => {
    const t = setup();
    t.controller.start();

    await t.respond(0, tooMany());
    await t.advance(20_000 - 1);
    expect(t.calls).toHaveLength(1);
    await t.advance(1);
    expect(t.calls).toHaveLength(2);

    await t.respond(1, tooMany());
    await t.advance(40_000 - 1);
    expect(t.calls).toHaveLength(2);
    await t.advance(1);
    expect(t.calls).toHaveLength(3);

    await t.respond(2, tooMany());
    await t.advance(60_000 - 1);
    expect(t.calls).toHaveLength(3);
    await t.advance(1);
    expect(t.calls).toHaveLength(4);

    await t.respond(3, tooMany());
    await t.advance(60_000 - 1);
    expect(t.calls).toHaveLength(4);
    await t.advance(1);
    expect(t.calls).toHaveLength(5);
  });

  it("uses Retry-After when it is present", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, tooMany("30"));
    await t.advance(30_000 - 1);
    expect(t.calls).toHaveLength(1);
    await t.advance(1);
    expect(t.calls).toHaveLength(2);
  });

  it("uses a Retry-After HTTP date relative to the injected clock", async () => {
    const t = setup();
    t.setNow(Date.UTC(2026, 10, 15, 2, 0, 0));
    t.controller.start();
    await t.respond(0, tooMany("Sun, 15 Nov 2026 02:00:25 GMT"));
    await t.advance(25_000 - 1);
    expect(t.calls).toHaveLength(1);
    await t.advance(1);
    expect(t.calls).toHaveLength(2);
  });

  it("returns to the normal interval after a success", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, tooMany());
    await t.advance(20_000);
    await t.respond(1, ok);
    await t.advance(BASE - 1);
    expect(t.calls).toHaveLength(2);
    await t.advance(1);
    expect(t.calls).toHaveLength(3);
  });

  it("uses the normal interval after a non-429 failure", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, serverError);
    await t.advance(BASE - 1);
    expect(t.calls).toHaveLength(1);
    await t.advance(1);
    expect(t.calls).toHaveLength(2);

    await t.respond(1, networkError);
    await t.advance(BASE);
    expect(t.calls).toHaveLength(3);
  });

  it("backs off from the first 429, even though the overlay has not appeared yet", async () => {
    const t = setup();
    t.controller.start();
    await t.respond(0, ok);
    await t.advance(BASE);
    await t.respond(1, tooMany());
    expect(toView(t.controller.getState()).kind).toBe("live"); // only 1 failure
    await t.advance(20_000 - 1);
    expect(t.calls).toHaveLength(2);
    await t.advance(1);
    expect(t.calls).toHaveLength(3);
  });
});

describe("PollController: stale and recovery transitions", () => {
  /** Runs outcomes one after another, waiting one interval between them, and returns the views seen. */
  async function runOutcomes(outcomes: FetchOutcome[]) {
    const t = setup();
    t.controller.start();
    for (let i = 0; i < outcomes.length; i++) {
      t.setNow(T0 + (i + 1) * 1_000);
      await t.respond(i, outcomes[i]);
      await t.advance(60_000); // long enough for any backoff
    }
    return { t, kinds: t.changes.map((state) => toView(state).kind) };
  }

  it("one failure between two successes never reaches no-signal", async () => {
    const { kinds } = await runOutcomes([ok, networkError, ok]);
    expect(kinds).toEqual(["live", "live", "live"]);
  });

  it("two failures show no-signal, and the next success recovers without user action", async () => {
    const { kinds } = await runOutcomes([ok, networkError, serverError, ok]);
    expect(kinds).toEqual(["live", "live", "no-signal", "live"]);
  });

  it("keeps the first stale time while failures continue", async () => {
    const { t } = await runOutcomes([ok, networkError, networkError, networkError]);
    const view = toView(t.controller.getState());
    expect(view).toEqual({ kind: "no-signal", data: DATA, staleSinceMs: T0 + 1_000 });
  });

  it("shows loading after one failure and no-signal after two when there was never good data", async () => {
    const { kinds } = await runOutcomes([networkError, networkError]);
    expect(kinds).toEqual(["loading", "no-signal"]);
  });

  it("recovers from the first-load failure on the first success", async () => {
    const { kinds } = await runOutcomes([networkError, networkError, ok]);
    expect(kinds).toEqual(["loading", "no-signal", "live"]);
  });

  it("treats an invalid response like any other failure", async () => {
    const invalid: FetchOutcome = {
      kind: "failure",
      reason: { kind: "invalid_shape", field: "services[0].status" },
      retryAfter: null,
    };
    const { t, kinds } = await runOutcomes([ok, invalid, invalid]);
    expect(kinds).toEqual(["live", "live", "no-signal"]);
    expect(t.controller.getState().data).toBe(DATA); // the last good data is kept
  });
});

describe("PollController: unexpected errors from fetchStatus", () => {
  it("treats a rejected promise as a network failure and keeps polling", async () => {
    const changes: PollState[] = [];
    let attempts = 0;
    const controller = new PollController({
      fetchStatus: () => {
        attempts += 1;
        return Promise.reject(new Error("unexpected"));
      },
      baseIntervalMs: BASE,
      now: () => T0,
      visibility: new FakeVisibility(),
      onChange: (state) => changes.push(state),
    });
    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(changes).toHaveLength(1);
    expect(changes[0].lastFailure).toEqual({ kind: "network" });
    await vi.advanceTimersByTimeAsync(BASE);
    expect(attempts).toBe(2);
    controller.stop();
  });

  it("treats a synchronous throw as a network failure and keeps polling", async () => {
    const changes: PollState[] = [];
    let attempts = 0;
    const controller = new PollController({
      fetchStatus: () => {
        attempts += 1;
        throw new Error("unexpected");
      },
      baseIntervalMs: BASE,
      now: () => T0,
      visibility: new FakeVisibility(),
      onChange: (state) => changes.push(state),
    });
    controller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(changes).toHaveLength(1);
    expect(changes[0].lastFailure).toEqual({ kind: "network" });
    await vi.advanceTimersByTimeAsync(BASE);
    expect(attempts).toBe(2);
    controller.stop();
  });
});
