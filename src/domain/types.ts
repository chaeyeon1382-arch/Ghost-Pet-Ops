export type PetState = "healthy" | "slow" | "down" | "recovering";

/** Result of one health check. statusCode is null when the connection failed. */
export interface CheckResult {
  statusCode: number | null;
  responseMs: number;
  timedOut: boolean;
}

/** Everything the decision logic remembers between checks. */
export interface Tracker {
  state: PetState;
  slowStreak: number; // consecutive successes slower than SLOW_THRESHOLD_MS
  fastStreak: number; // consecutive successes at or under SLOW_THRESHOLD_MS
  failStreak: number; // consecutive failures
}

export interface Transition {
  from: PetState;
  to: PetState;
  note: string;
}

export interface Decision {
  tracker: Tracker;
  transition?: Transition;
}
