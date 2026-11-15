import { nextDelay } from "./pollSchedule";
import { applyOutcome, initialPollState } from "./statusState";
import type { FetchOutcome, PollState } from "./types";

/** Where the controller learns whether the page is hidden. The hook wraps `document`. Tests pass a fake. */
export interface VisibilitySource {
  isHidden(): boolean;
  /** Calls `listener` whenever visibility changes. Returns a function that removes the listener. */
  subscribe(listener: () => void): () => void;
}

export interface PollControllerDeps {
  fetchStatus(signal: AbortSignal): Promise<FetchOutcome>;
  baseIntervalMs: number;
  now(): number;
  visibility: VisibilitySource;
  /** Called with the new state after every request that changed it. Never called after `stop()`. */
  onChange(state: PollState): void;
}

const NETWORK_FAILURE: FetchOutcome = { kind: "failure", reason: { kind: "network" }, retryAfter: null };

/**
 * Polls /status. No React: it only uses timers, AbortController and the injected dependencies.
 *
 * - One request at a time. The next request is scheduled after the previous one finishes.
 * - While the page is hidden there is no timer and no request. When it is visible again, it requests at once.
 * - `start()` is idempotent and `stop()` is safe to call any number of times.
 * - After `stop()` nothing is requested, no timer fires, and no state is published,
 *   including for a response that was already in flight.
 */
export class PollController {
  private readonly deps: PollControllerDeps;
  private running = false;
  /** True while the page is hidden and the loop is waiting for it to become visible. */
  private paused = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** The request that is currently waiting for a response. A response from any other request is ignored. */
  private inFlight: AbortController | null = null;
  private currentMs: number;
  private state: PollState = initialPollState;
  private unsubscribe: (() => void) | null = null;

  constructor(deps: PollControllerDeps) {
    this.deps = deps;
    this.currentMs = deps.baseIntervalMs;
  }

  getState(): PollState {
    return this.state;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.currentMs = this.deps.baseIntervalMs;
    this.unsubscribe = this.deps.visibility.subscribe(() => this.onVisibilityChange());
    if (this.deps.visibility.isHidden()) {
      this.paused = true;
    } else {
      this.request();
    }
  }

  stop(): void {
    this.running = false;
    this.paused = false;
    this.clearTimer();
    this.abortInFlight();
    if (this.unsubscribe !== null) {
      const unsubscribe = this.unsubscribe;
      this.unsubscribe = null;
      unsubscribe();
    }
  }

  private onVisibilityChange(): void {
    if (!this.running) return;
    if (this.deps.visibility.isHidden()) {
      this.paused = true;
      this.clearTimer();
      this.abortInFlight(); // an aborted request is not a failure
    } else if (this.paused) {
      this.paused = false;
      this.request();
    }
  }

  private request(): void {
    if (!this.running || this.paused || this.inFlight !== null) return;
    this.clearTimer();
    const controller = new AbortController();
    this.inFlight = controller;

    let pending: Promise<FetchOutcome>;
    try {
      pending = this.deps.fetchStatus(controller.signal);
    } catch {
      pending = Promise.resolve(NETWORK_FAILURE);
    }
    pending.then(
      (outcome) => this.onOutcome(controller, outcome),
      () => this.onOutcome(controller, NETWORK_FAILURE),
    );
  }

  private onOutcome(request: AbortController, outcome: FetchOutcome): void {
    // The request was aborted by stop() or by the page being hidden. Its response is ignored.
    if (this.inFlight !== request) return;
    this.inFlight = null;

    const nowMs = this.deps.now();
    const previous = this.state;
    this.state = applyOutcome(previous, outcome, nowMs);
    this.currentMs = nextDelay({ outcome, baseMs: this.deps.baseIntervalMs, currentMs: this.currentMs, nowMs });

    // Schedule first, so a listener that throws cannot stop the loop.
    if (this.running && !this.paused) {
      this.timer = setTimeout(() => {
        this.timer = null;
        this.request();
      }, this.currentMs);
    }
    if (this.state !== previous) this.deps.onChange(this.state);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private abortInFlight(): void {
    const request = this.inFlight;
    this.inFlight = null; // cleared first, so the response of this request is ignored
    if (request !== null) request.abort();
  }
}
