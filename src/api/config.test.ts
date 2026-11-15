import { describe, expect, it } from "vitest";
import {
  DEFAULT_POLL_MS,
  MAX_POLL_MS,
  MIN_POLL_MS,
  readConfig,
  resolveDataSource,
  resolvePollInterval,
} from "./config";

describe("resolveDataSource", () => {
  it("selects api only for the exact string 'api'", () => {
    expect(resolveDataSource("api")).toBe("api");
  });

  it("defaults to mock for unset, empty and other values", () => {
    const others: unknown[] = [undefined, null, "", "mock", "API", " api", "real", "true", 1, true, {}];
    for (const raw of others) {
      expect(resolveDataSource(raw)).toBe("mock");
    }
  });
});

describe("resolvePollInterval", () => {
  it("defaults to 10 seconds", () => {
    expect(DEFAULT_POLL_MS).toBe(10_000);
    expect(resolvePollInterval(undefined)).toBe(10_000);
  });

  it("keeps values inside the 5 to 10 second range", () => {
    expect(resolvePollInterval(5_000)).toBe(5_000);
    expect(resolvePollInterval(7_000)).toBe(7_000);
    expect(resolvePollInterval(10_000)).toBe(10_000);
  });

  it("clamps values outside the range", () => {
    expect(resolvePollInterval(0)).toBe(MIN_POLL_MS);
    expect(resolvePollInterval(1_000)).toBe(MIN_POLL_MS);
    expect(resolvePollInterval(-5)).toBe(MIN_POLL_MS);
    expect(resolvePollInterval(60_000)).toBe(MAX_POLL_MS);
  });

  it("accepts numeric strings", () => {
    expect(resolvePollInterval("7000")).toBe(7_000);
    expect(resolvePollInterval(" 7000 ")).toBe(7_000);
    expect(resolvePollInterval("1000")).toBe(MIN_POLL_MS);
    expect(resolvePollInterval("99999")).toBe(MAX_POLL_MS);
  });

  it("falls back to the default for values that are not usable numbers", () => {
    const junk: unknown[] = [undefined, null, "", "   ", "abc", "10s", NaN, Infinity, -Infinity, true, {}, []];
    for (const raw of junk) {
      expect(resolvePollInterval(raw)).toBe(DEFAULT_POLL_MS);
    }
  });
});

describe("readConfig", () => {
  it("uses the mock source and the default interval when nothing is set", () => {
    expect(readConfig({})).toEqual({ dataSource: "mock", baseIntervalMs: 10_000 });
  });

  it("reads both variables", () => {
    const config = readConfig({ VITE_DATA_SOURCE: "api", VITE_POLL_INTERVAL_MS: "7000" });
    expect(config).toEqual({ dataSource: "api", baseIntervalMs: 7_000 });
  });

  it("ignores variables it does not know", () => {
    expect(readConfig({ SOME_OTHER_SETTING: "healthy", VITE_OTHER: "x" })).toEqual({
      dataSource: "mock",
      baseIntervalMs: 10_000,
    });
  });
});
