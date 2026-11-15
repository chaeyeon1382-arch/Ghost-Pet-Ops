export type Status = "unknown" | "healthy" | "slow" | "down" | "recovering";

/** Result of one health check. statusCode is null when the connection failed. */
export interface CheckResult {
  statusCode: number | null;
  responseMs: number;
  timedOut: boolean;
}

export type CheckClass = "failure" | "fast" | "slow";

/**
 * Everything the decision logic remembers between checks.
 * The checker Lambda persists this per service. It is not part of the /status response.
 */
export interface Tracker {
  status: Status;
  consecutiveFailures: number;
  consecutiveSlow: number;
  consecutiveFast: number;
}

export interface Transition {
  from: Status;
  to: Status;
  note: string;
}

export interface Decision {
  tracker: Tracker;
  transition?: Transition;
}
