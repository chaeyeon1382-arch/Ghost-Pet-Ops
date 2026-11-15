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
