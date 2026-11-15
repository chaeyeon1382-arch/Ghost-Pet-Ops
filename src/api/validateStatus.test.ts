import { describe, expect, it } from "vitest";
import { MAX_EVENTS } from "./types";
import { isUtcTimestamp, validateStatus } from "./validateStatus";

// Fixed fixtures only. Nothing here reads the clock, the time zone or the network.
const SERVICE = {
  id: "site-1",
  name: "Example",
  status: "healthy",
  responseMs: 84,
  lastChecked: "2026-11-15T02:00:00Z",
};

const EVENT = {
  serviceId: "site-1",
  from: "healthy",
  to: "slow",
  at: "2026-11-15T01:58:00Z",
  note: "response over 2s twice in a row",
};

function withService(patch: Record<string, unknown>) {
  return { services: [{ ...SERVICE, ...patch }], events: [] };
}

function withEvent(patch: Record<string, unknown>) {
  return { services: [{ ...SERVICE }], events: [{ ...EVENT, ...patch }] };
}

/** Returns the failing field path, or null when the body is valid. */
function failingField(body: unknown): string | null {
  const result = validateStatus(body);
  return result.ok ? null : result.field;
}

function accepted(body: unknown) {
  const result = validateStatus(body);
  if (!result.ok) throw new Error(`expected a valid body, got an error at ${result.field}`);
  return result.data;
}

describe("validateStatus: valid bodies", () => {
  it("accepts a normal response and returns the same data", () => {
    const body = { services: [{ ...SERVICE }], events: [{ ...EVENT }] };
    expect(accepted(body)).toStrictEqual({ services: [SERVICE], events: [EVENT] });
  });

  it("accepts unknown with null responseMs and lastChecked, and no events", () => {
    const body = {
      services: [{ id: "site-1", name: "Example", status: "unknown", responseMs: null, lastChecked: null }],
      events: [],
    };
    expect(accepted(body).services[0]).toStrictEqual({
      id: "site-1",
      name: "Example",
      status: "unknown",
      responseMs: null,
      lastChecked: null,
    });
  });

  it("accepts null responseMs and lastChecked for any status", () => {
    expect(failingField(withService({ status: "healthy", responseMs: null, lastChecked: null }))).toBeNull();
  });

  it("accepts all five statuses", () => {
    for (const status of ["unknown", "healthy", "slow", "down", "recovering"]) {
      expect(failingField(withService({ status }))).toBeNull();
    }
  });

  it("accepts timestamps with whole seconds and with fractional seconds", () => {
    expect(failingField(withService({ lastChecked: "2026-11-15T02:00:00Z" }))).toBeNull();
    expect(failingField(withService({ lastChecked: "2026-11-15T02:00:00.000Z" }))).toBeNull();
    expect(failingField(withEvent({ at: "2026-11-15T01:58:00.123Z" }))).toBeNull();
  });

  it("accepts responseMs of 0", () => {
    expect(failingField(withService({ responseMs: 0 }))).toBeNull();
  });

  it("accepts exactly MAX_EVENTS events", () => {
    const events = Array.from({ length: MAX_EVENTS }, (_, i) => ({
      ...EVENT,
      at: `2026-11-15T01:${String(i).padStart(2, "0")}:00Z`,
    }));
    expect(accepted({ services: [{ ...SERVICE }], events }).events).toHaveLength(MAX_EVENTS);
  });

  it("accepts an event whose serviceId is not in the services list", () => {
    expect(failingField(withEvent({ serviceId: "removed-site" }))).toBeNull();
  });

  it("accepts several services and keeps their order", () => {
    const body = {
      services: [
        { ...SERVICE, id: "b", name: "B" },
        { ...SERVICE, id: "a", name: "A" },
      ],
      events: [],
    };
    expect(accepted(body).services.map((s) => s.id)).toEqual(["b", "a"]);
  });

  it("keeps events in the order received", () => {
    const body = {
      services: [{ ...SERVICE }],
      events: [
        { ...EVENT, at: "2026-11-15T01:00:00Z" },
        { ...EVENT, at: "2026-11-15T01:30:00Z" },
      ],
    };
    expect(accepted(body).events.map((e) => e.at)).toEqual(["2026-11-15T01:00:00Z", "2026-11-15T01:30:00Z"]);
  });

  it("drops extra fields at every level", () => {
    const body = {
      version: 2,
      services: [{ ...SERVICE, consecutiveFailures: 3, extra: { nested: true } }],
      events: [{ ...EVENT, internal: "x" }],
    };
    expect(accepted(body)).toStrictEqual({ services: [SERVICE], events: [EVENT] });
  });

  it("returns new objects, not the input", () => {
    const body = { services: [{ ...SERVICE }], events: [{ ...EVENT }] };
    const data = accepted(body);
    expect(data.services[0]).not.toBe(body.services[0]);
    expect(data.events[0]).not.toBe(body.events[0]);
  });
});

describe("validateStatus: body and lists", () => {
  it("rejects a body that is not an object", () => {
    const bodies: unknown[] = [null, undefined, [], "text", 42, true];
    for (const body of bodies) {
      expect(failingField(body)).toBe("body");
    }
  });

  it("rejects a missing or non-array services", () => {
    expect(failingField({ events: [] })).toBe("services");
    expect(failingField({ services: "x", events: [] })).toBe("services");
    expect(failingField({ services: {}, events: [] })).toBe("services");
  });

  it("rejects an empty services list", () => {
    expect(failingField({ services: [], events: [] })).toBe("services");
  });

  it("rejects a missing or non-array events", () => {
    expect(failingField({ services: [{ ...SERVICE }] })).toBe("events");
    expect(failingField({ services: [{ ...SERVICE }], events: null })).toBe("events");
    expect(failingField({ services: [{ ...SERVICE }], events: {} })).toBe("events");
  });

  it("rejects more than MAX_EVENTS events", () => {
    const events = Array.from({ length: MAX_EVENTS + 1 }, () => ({ ...EVENT }));
    expect(failingField({ services: [{ ...SERVICE }], events })).toBe("events");
  });

  it("rejects duplicate service ids and points at the second one", () => {
    const body = { services: [{ ...SERVICE }, { ...SERVICE, name: "Other" }], events: [] };
    expect(failingField(body)).toBe("services[1].id");
  });

  it("rejects a service or event that is not an object", () => {
    expect(failingField({ services: ["x"], events: [] })).toBe("services[0]");
    expect(failingField({ services: [null], events: [] })).toBe("services[0]");
    expect(failingField({ services: [{ ...SERVICE }], events: [[]] })).toBe("events[0]");
  });
});

describe("validateStatus: service fields", () => {
  it("rejects uppercase and mixed-case statuses without normalizing them", () => {
    expect(failingField(withService({ status: "Healthy" }))).toBe("services[0].status");
    expect(failingField(withService({ status: "HEALTHY" }))).toBe("services[0].status");
    expect(failingField(withService({ status: "Down" }))).toBe("services[0].status");
  });

  it("rejects a sixth status and non-string statuses", () => {
    expect(failingField(withService({ status: "degraded" }))).toBe("services[0].status");
    expect(failingField(withService({ status: "" }))).toBe("services[0].status");
    expect(failingField(withService({ status: 1 }))).toBe("services[0].status");
    expect(failingField(withService({ status: null }))).toBe("services[0].status");
  });

  it("rejects status names that exist on every object", () => {
    for (const status of ["constructor", "toString", "__proto__", "hasOwnProperty"]) {
      expect(failingField(withService({ status }))).toBe("services[0].status");
    }
  });

  it("rejects a missing field", () => {
    for (const key of ["id", "name", "status", "responseMs", "lastChecked"]) {
      const service: Record<string, unknown> = { ...SERVICE };
      delete service[key];
      expect(failingField({ services: [service], events: [] })).toBe(`services[0].${key}`);
    }
  });

  it("rejects wrong types for id and name", () => {
    expect(failingField(withService({ id: 1 }))).toBe("services[0].id");
    expect(failingField(withService({ name: null }))).toBe("services[0].name");
  });

  it("rejects responseMs that is negative, fractional, not finite or not a number", () => {
    const bad: unknown[] = [-1, 1.5, NaN, Infinity, "84", true, {}];
    for (const responseMs of bad) {
      expect(failingField(withService({ responseMs }))).toBe("services[0].responseMs");
    }
  });

  it("rejects lastChecked that is not a UTC timestamp with Z", () => {
    const bad: unknown[] = [
      "2026-11-15T02:00:00",
      "2026-11-15T11:00:00+09:00",
      "2026-11-15 02:00:00Z",
      "2026-11-15T02:00Z",
      "2026-11-15",
      "",
      1763172000,
      true,
    ];
    for (const lastChecked of bad) {
      expect(failingField(withService({ lastChecked }))).toBe("services[0].lastChecked");
    }
  });

  it("points at the right service when a later one is bad", () => {
    const body = { services: [{ ...SERVICE }, { ...SERVICE, id: "site-2", status: "Down" }], events: [] };
    expect(failingField(body)).toBe("services[1].status");
  });
});

describe("validateStatus: event fields", () => {
  it("rejects bad from and to values", () => {
    expect(failingField(withEvent({ from: "Down" }))).toBe("events[0].from");
    expect(failingField(withEvent({ to: "broken" }))).toBe("events[0].to");
  });

  it("rejects a bad at", () => {
    expect(failingField(withEvent({ at: "2026-11-15T01:58:00+00:00" }))).toBe("events[0].at");
    expect(failingField(withEvent({ at: 12345 }))).toBe("events[0].at");
  });

  it("rejects wrong types for serviceId and note", () => {
    expect(failingField(withEvent({ serviceId: 7 }))).toBe("events[0].serviceId");
    expect(failingField(withEvent({ note: null }))).toBe("events[0].note");
  });

  it("rejects a missing field", () => {
    for (const key of ["serviceId", "from", "to", "at", "note"]) {
      const event: Record<string, unknown> = { ...EVENT };
      delete event[key];
      expect(failingField({ services: [{ ...SERVICE }], events: [event] })).toBe(`events[0].${key}`);
    }
  });

  it("points at the right event when a later one is bad", () => {
    const body = { services: [{ ...SERVICE }], events: [{ ...EVENT }, { ...EVENT, to: "nope" }] };
    expect(failingField(body)).toBe("events[1].to");
  });
});

describe("validateStatus: error value", () => {
  it("names the field and never includes the offending value", () => {
    const result = validateStatus(withService({ status: "SECRET-TOKEN-VALUE" }));
    expect(result.ok).toBe(false);
    expect(JSON.stringify(result)).not.toContain("SECRET-TOKEN-VALUE");
  });

  it("does not throw for any kind of input", () => {
    const inputs: unknown[] = [undefined, null, 0, "", [], {}, { services: [undefined], events: [undefined] }, () => 1];
    for (const input of inputs) {
      expect(() => validateStatus(input)).not.toThrow();
    }
  });
});

describe("isUtcTimestamp", () => {
  it("accepts real UTC timestamps", () => {
    expect(isUtcTimestamp("2026-11-15T02:00:00Z")).toBe(true);
    expect(isUtcTimestamp("2026-11-15T23:59:59Z")).toBe(true);
    expect(isUtcTimestamp("2026-11-15T00:00:00.000Z")).toBe(true);
    expect(isUtcTimestamp("2028-02-29T00:00:00Z")).toBe(true); // 2028 is a leap year
  });

  it("rejects impossible dates and times", () => {
    expect(isUtcTimestamp("2026-02-30T00:00:00Z")).toBe(false);
    expect(isUtcTimestamp("2026-02-29T00:00:00Z")).toBe(false); // 2026 is not a leap year
    expect(isUtcTimestamp("2026-04-31T00:00:00Z")).toBe(false);
    expect(isUtcTimestamp("2026-13-01T00:00:00Z")).toBe(false);
    expect(isUtcTimestamp("2026-00-10T00:00:00Z")).toBe(false);
    expect(isUtcTimestamp("2026-11-00T00:00:00Z")).toBe(false);
    expect(isUtcTimestamp("2026-11-15T24:00:00Z")).toBe(false);
    expect(isUtcTimestamp("2026-11-15T02:60:00Z")).toBe(false);
    expect(isUtcTimestamp("2026-11-15T02:00:60Z")).toBe(false);
  });

  it("rejects other formats", () => {
    expect(isUtcTimestamp("2026-11-15T02:00:00")).toBe(false);
    expect(isUtcTimestamp("2026-11-15T02:00:00z")).toBe(false);
    expect(isUtcTimestamp("2026-11-15T02:00:00.Z")).toBe(false);
    expect(isUtcTimestamp(" 2026-11-15T02:00:00Z")).toBe(false);
    expect(isUtcTimestamp(null)).toBe(false);
    expect(isUtcTimestamp(undefined)).toBe(false);
  });
});
