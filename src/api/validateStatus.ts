import type { Status } from "../domain/types";
import { MAX_EVENTS, type ServiceStatus, type StateEvent, type StatusResponse } from "./types";

/**
 * Checks a parsed /status body against .kiro/steering/api-format.md.
 * Pure: no I/O, never throws. On failure it returns the path of the first bad field
 * (for example "services[1].status") and never the offending value.
 * Extra fields are dropped: the result is built only from the known fields.
 */
export type ValidationResult = { ok: true; data: StatusResponse } | { ok: false; field: string };

type Fail = { ok: false; field: string };
type Parsed<T> = { ok: true; value: T } | Fail;

// A Record keyed by Status makes the compiler flag this list if the Status type ever changes.
const STATUS_SET: Record<Status, true> = {
  unknown: true,
  healthy: true,
  slow: true,
  down: true,
  recovering: true,
};

const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/;

const fail = (field: string): Fail => ({ ok: false, field });
const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const isStatus = (v: unknown): v is Status => typeof v === "string" && Object.hasOwn(STATUS_SET, v);

/**
 * UTC ISO 8601 with a "Z" suffix. The backend sends whole seconds (YYYY-MM-DDTHH:MM:SSZ).
 * A fractional part is accepted because the mock builder's toISOString() emits ".000Z".
 * The value must also be a real calendar date and time.
 */
export function isUtcTimestamp(v: unknown): v is string {
  if (typeof v !== "string") return false;
  const m = TIMESTAMP.exec(v);
  if (!m) return false;
  const [year, month, day, hour, minute, second] = m.slice(1, 7).map(Number);
  if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) return false;
  // Date.UTC rolls an impossible day (Feb 30) over into the next month, so compare the parts again.
  const d = new Date(Date.UTC(year, month - 1, day));
  return d.getUTCFullYear() === year && d.getUTCMonth() === month - 1 && d.getUTCDate() === day;
}

function parseService(v: unknown, path: string): Parsed<ServiceStatus> {
  if (!isRecord(v)) return fail(path);
  if (typeof v.id !== "string") return fail(`${path}.id`);
  if (typeof v.name !== "string") return fail(`${path}.name`);
  if (!isStatus(v.status)) return fail(`${path}.status`);
  const responseMs = v.responseMs;
  if (responseMs !== null && !(typeof responseMs === "number" && Number.isInteger(responseMs) && responseMs >= 0)) {
    return fail(`${path}.responseMs`);
  }
  const lastChecked = v.lastChecked;
  if (lastChecked !== null && !isUtcTimestamp(lastChecked)) return fail(`${path}.lastChecked`);
  return { ok: true, value: { id: v.id, name: v.name, status: v.status, responseMs, lastChecked } };
}

function parseEvent(v: unknown, path: string): Parsed<StateEvent> {
  if (!isRecord(v)) return fail(path);
  if (typeof v.serviceId !== "string") return fail(`${path}.serviceId`);
  if (!isStatus(v.from)) return fail(`${path}.from`);
  if (!isStatus(v.to)) return fail(`${path}.to`);
  if (!isUtcTimestamp(v.at)) return fail(`${path}.at`);
  if (typeof v.note !== "string") return fail(`${path}.note`);
  return { ok: true, value: { serviceId: v.serviceId, from: v.from, to: v.to, at: v.at, note: v.note } };
}

export function validateStatus(body: unknown): ValidationResult {
  if (!isRecord(body)) return fail("body");
  const { services, events } = body;
  if (!Array.isArray(services) || services.length === 0) return fail("services");
  if (!Array.isArray(events) || events.length > MAX_EVENTS) return fail("events");

  const outServices: ServiceStatus[] = [];
  const seenIds = new Set<string>();
  for (let i = 0; i < services.length; i++) {
    const parsed = parseService(services[i], `services[${i}]`);
    if (!parsed.ok) return parsed;
    if (seenIds.has(parsed.value.id)) return fail(`services[${i}].id`);
    seenIds.add(parsed.value.id);
    outServices.push(parsed.value);
  }

  // Events keep the order received. The API defines it as newest first.
  const outEvents: StateEvent[] = [];
  for (let i = 0; i < events.length; i++) {
    const parsed = parseEvent(events[i], `events[${i}]`);
    if (!parsed.ok) return parsed;
    outEvents.push(parsed.value);
  }

  return { ok: true, data: { services: outServices, events: outEvents } };
}
