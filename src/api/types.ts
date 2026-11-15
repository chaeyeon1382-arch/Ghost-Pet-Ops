import type { Status } from "../domain/types";

/** Shape returned by GET /status (mocked for now, API Gateway later). See .kiro/steering/api-format.md. */
export interface ServiceStatus {
  id: string;
  name: string;
  status: Status;
  /** null until the first check has run */
  responseMs: number | null;
  /** ISO 8601, UTC. null until the first check has run. */
  lastChecked: string | null;
}

export interface StateEvent {
  serviceId: string;
  from: Status;
  to: Status;
  at: string; // ISO 8601, UTC
  note: string;
}

export interface StatusResponse {
  services: ServiceStatus[];
  /** Newest first, at most MAX_EVENTS. */
  events: StateEvent[];
}

export const MAX_EVENTS = 20;

/** Why a /status request failed. Never contains the response body. */
export type FailureReason =
  | { kind: "network" }
  | { kind: "http"; status: number }
  | { kind: "invalid_json" }
  | { kind: "invalid_shape"; field: string }; // field path only

/** Result of one /status request. `retryAfter` is the raw header value, read only for 429. */
export type FetchOutcome =
  | { kind: "success"; data: StatusResponse }
  | { kind: "failure"; reason: FailureReason; retryAfter: string | null }
  | { kind: "aborted" };

/** What the polling logic knows. `data` is the last good response. */
export interface PollState {
  data: StatusResponse | null;
  /** Epoch ms when `data` was received. */
  receivedAt: number | null;
  /** Failed requests since the last valid response. */
  consecutiveFailures: number;
  lastFailure: FailureReason | null;
}

/**
 * What the UI shows. `no-signal` is a UI state, not a pet status.
 * `staleSinceMs` is null when no good data was ever received.
 */
export type StatusView =
  | { kind: "loading" }
  | { kind: "live"; data: StatusResponse }
  | { kind: "no-signal"; data: StatusResponse | null; staleSinceMs: number | null };
