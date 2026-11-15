import type { PetState } from "../domain/types";

/** Shape returned by GET /status (mocked for now, API Gateway later). */
export interface ServiceStatus {
  id: string;
  name: string;
  status: PetState;
  responseMs: number;
  lastChecked: string; // ISO 8601, UTC
}

export interface StateEvent {
  serviceId: string;
  from: PetState;
  to: PetState;
  at: string; // ISO 8601, UTC
  note: string;
}

export interface StatusResponse {
  services: ServiceStatus[];
  events: StateEvent[];
}
