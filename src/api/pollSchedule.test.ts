import { describe, expect, it } from "vitest";
import { MAX_BACKOFF_MS } from "./config";
import { nextDelay, parseRetryAfter } from "./pollSchedule";
import type { FetchOutcome, StatusResponse } from "./types";

// Fixed values only. "Now" is a constant passed in, never read from the clock.
const BASE = 10_000;
const NOW = Date.UTC(2026, 10, 15, 2, 0, 0); // 2026-11-15T02:00:00Z, a Sunday

const DATA: StatusResponse = { services: [], events: [] };
const success: FetchOutcome = { kind: "success", data: DATA };
const aborted: FetchOutcome = { kind: "aborted" };

function http(status: number, retryAfter: string | null = null): FetchOutcome {
  return { kind: "failure", reason: { kind: "http", status }, retryAfter };
}

function delay(outcome: FetchOutcome, currentMs: number, baseMs = BASE) {
  return nextDelay({ outcome, baseMs, currentMs, nowMs: NOW });
}

describe("parseRetryAfter: seconds", () => {
  it("reads whole seconds and returns milliseconds", () => {
    expect(parseRetryAfter("120", NOW)).toBe(120_000);
    expect(parseRetryAfter("1", NOW)).toBe(1_000);
    expect(parseRetryAfter("0", NOW)).toBe(0);
  });

  it("ignores surrounding spaces", () => {
    expect(parseRetryAfter(" 30 ", NOW)).toBe(30_000);
  });
});

describe("parseRetryAfter: HTTP dates", () => {
  it("returns the time until a future date", () => {
    expect(parseRetryAfter("Sun, 15 Nov 2026 02:00:30 GMT", NOW)).toBe(30_000);
    expect(parseRetryAfter("Sun, 15 Nov 2026 02:05:00 GMT", NOW)).toBe(300_000);
  });

  it("accepts the older RFC 850 date form", () => {
    expect(parseRetryAfter("Sunday, 15-Nov-26 02:00:30 GMT", NOW)).toBe(30_000);
  });

  it("returns 0 for a date that is now or in the past", () => {
    expect(parseRetryAfter("Sun, 15 Nov 2026 02:00:00 GMT", NOW)).toBe(0);
    expect(parseRetryAfter("Sun, 15 Nov 2026 01:59:00 GMT", NOW)).toBe(0);
  });
});

describe("parseRetryAfter: invalid values", () => {
  it("returns null when the header is absent", () => {
    expect(parseRetryAfter(null, NOW)).toBeNull();
  });

  it("returns null for empty and blank values", () => {
    expect(parseRetryAfter("", NOW)).toBeNull();
    expect(parseRetryAfter("   ", NOW)).toBeNull();
  });

  it("returns null for negative, fractional and non-numeric values", () => {
    const bad = ["-5", "1.5", "1e3", "abc", "10s", "+5", "0x10"];
    for (const raw of bad) {
      expect(parseRetryAfter(raw, NOW)).toBeNull();
    }
  });

  it("returns null for strings that Date.parse would wrongly accept as dates", () => {
    // Date.parse("1.5"), Date.parse("-5") and Date.parse("Nov 5") all give a date in V8.
    expect(parseRetryAfter("Nov 5", NOW)).toBeNull();
    expect(parseRetryAfter("2026-11-15T02:00:30Z", NOW)).toBeNull();
  });

  it("returns null for a date without GMT, because it would be read in the local time zone", () => {
    expect(parseRetryAfter("Sun, 15 Nov 2026 02:00:30", NOW)).toBeNull();
    expect(parseRetryAfter("Sun Nov 15 02:00:30 2026", NOW)).toBeNull();
  });

  it("returns null for an impossible HTTP date", () => {
    expect(parseRetryAfter("Sun, 99 Nov 2026 02:00:30 GMT", NOW)).toBeNull();
  });
});

describe("nextDelay: success and other failures", () => {
  it("returns the base interval after a success", () => {
    expect(delay(success, BASE)).toBe(BASE);
    expect(delay(success, 40_000)).toBe(BASE);
  });

  it("returns the base interval after non-429 failures", () => {
    const failures: FetchOutcome[] = [
      http(500),
      http(503),
      http(404),
      http(403),
      { kind: "failure", reason: { kind: "network" }, retryAfter: null },
      { kind: "failure", reason: { kind: "invalid_json" }, retryAfter: null },
      { kind: "failure", reason: { kind: "invalid_shape", field: "services" }, retryAfter: null },
    ];
    for (const outcome of failures) {
      expect(delay(outcome, BASE)).toBe(BASE);
    }
  });

  it("ignores Retry-After on a non-429 failure", () => {
    expect(delay(http(503, "30"), BASE)).toBe(BASE);
  });

  it("returns a non-429 failure to the base interval even in the middle of a backoff", () => {
    expect(delay(http(500), 40_000)).toBe(BASE);
  });

  it("keeps the current delay when the request was aborted", () => {
    expect(delay(aborted, 40_000)).toBe(40_000);
  });

  it("uses the base interval it is given", () => {
    expect(delay(success, 5_000, 5_000)).toBe(5_000);
    expect(delay(http(500), 5_000, 5_000)).toBe(5_000);
  });
});

describe("nextDelay: 429 without Retry-After", () => {
  it("doubles the current delay", () => {
    expect(delay(http(429), BASE)).toBe(20_000);
    expect(delay(http(429), 20_000)).toBe(40_000);
  });

  it("stops doubling at the 60 second cap", () => {
    expect(MAX_BACKOFF_MS).toBe(60_000);
    expect(delay(http(429), 40_000)).toBe(60_000);
    expect(delay(http(429), 60_000)).toBe(60_000);
  });

  it("follows 10, 20, 40, 60, 60 seconds for repeated 429 responses", () => {
    let current = BASE;
    const seen: number[] = [];
    for (let i = 0; i < 5; i++) {
      current = delay(http(429), current);
      seen.push(current);
    }
    expect(seen).toEqual([20_000, 40_000, 60_000, 60_000, 60_000]);
  });

  it("treats an invalid Retry-After like a missing one", () => {
    expect(delay(http(429, "abc"), BASE)).toBe(20_000);
    expect(delay(http(429, ""), BASE)).toBe(20_000);
    expect(delay(http(429, "-5"), BASE)).toBe(20_000);
  });

  it("returns to the base interval after a success, then doubles from the base again", () => {
    let current = delay(http(429), BASE);
    current = delay(http(429), current);
    expect(current).toBe(40_000);
    current = delay(success, current);
    expect(current).toBe(BASE);
    expect(delay(http(429), current)).toBe(20_000);
  });

  it("restarts from the base interval after a non-429 failure between two 429 responses", () => {
    let current = delay(http(429), BASE);
    expect(current).toBe(20_000);
    current = delay(http(500), current);
    expect(current).toBe(BASE);
    expect(delay(http(429), current)).toBe(20_000);
  });
});

describe("nextDelay: 429 with Retry-After", () => {
  it("uses the Retry-After seconds", () => {
    expect(delay(http(429, "30"), BASE)).toBe(30_000);
    expect(delay(http(429, "45"), 40_000)).toBe(45_000);
  });

  it("uses a Retry-After HTTP date", () => {
    expect(delay(http(429, "Sun, 15 Nov 2026 02:00:25 GMT"), BASE)).toBe(25_000);
  });

  it("caps a long Retry-After at 60 seconds", () => {
    expect(delay(http(429, "120"), BASE)).toBe(60_000);
    expect(delay(http(429, "99999999999999999999"), BASE)).toBe(60_000);
    expect(delay(http(429, "Mon, 16 Nov 2026 02:00:00 GMT"), BASE)).toBe(60_000);
  });

  it("raises a short Retry-After to the base interval", () => {
    expect(delay(http(429, "1"), BASE)).toBe(BASE);
    expect(delay(http(429, "0"), BASE)).toBe(BASE);
    expect(delay(http(429, "Sun, 15 Nov 2026 01:00:00 GMT"), BASE)).toBe(BASE);
  });

  it("uses the Retry-After value instead of doubling", () => {
    expect(delay(http(429, "15"), 40_000)).toBe(15_000);
  });

  it("applies the floor of the base interval it is given", () => {
    expect(delay(http(429, "2"), 5_000, 5_000)).toBe(5_000);
  });
});
