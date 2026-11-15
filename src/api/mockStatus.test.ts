import { describe, expect, it } from "vitest";
import { DEMO_SCENARIOS, buildMockResponse, buildResponseFromChecks } from "./mockStatus";
import { MAX_EVENTS } from "./types";
import type { CheckResult, Status } from "../domain/types";

const NOW = new Date("2026-11-15T02:00:00Z");

describe("buildMockResponse", () => {
  it("each demo scenario ends in its own status", () => {
    for (const s of Object.keys(DEMO_SCENARIOS) as Status[]) {
      expect(buildMockResponse(s, NOW).services[0].status).toBe(s);
    }
  });
  it("unknown has null responseMs and lastChecked and no events", () => {
    const r = buildMockResponse("unknown", NOW);
    expect(r.services[0].responseMs).toBe(null);
    expect(r.services[0].lastChecked).toBe(null);
    expect(r.events).toEqual([]);
  });
  it("uses UTC ISO 8601 times, events newest first", () => {
    const r = buildMockResponse("down", NOW);
    expect(r.services[0].lastChecked).toBe("2026-11-15T02:00:00.000Z");
    expect(r.events.map((e) => `${e.at} ${e.from}>${e.to}`)).toEqual([
      "2026-11-15T02:00:00.000Z healthy>down",
      "2026-11-15T01:57:00.000Z unknown>healthy",
    ]);
  });
  it("does not expose counters", () => {
    const service = buildMockResponse("slow", NOW).services[0];
    expect(Object.keys(service).sort()).toEqual(["id", "lastChecked", "name", "responseMs", "status"]);
  });
  it(`keeps at most ${MAX_EVENTS} events`, () => {
    const fail: CheckResult = { statusCode: 500, responseMs: 50, timedOut: false };
    const ok: CheckResult = { statusCode: 200, responseMs: 50, timedOut: false };
    // Each cycle: down -> recovering -> healthy -> down ... produces several events.
    const checks = Array.from({ length: 15 }, () => [ok, ok, fail, fail, fail]).flat();
    const r = buildResponseFromChecks(checks, NOW);
    expect(r.events.length).toBe(MAX_EVENTS);
    expect(r.events[0].at).toBe("2026-11-15T02:00:00.000Z");
  });
});
