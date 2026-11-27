import { describe, expect, it } from "vitest";
import { formatStaleSince } from "./staleSince";

// Every call passes an explicit time zone and locale, so nothing depends on the machine's settings.
// Instants are built with Date.UTC, so nothing reads the clock.
const en = (timeZone: string) => ({ timeZone, locale: "en-US" });

describe("formatStaleSince", () => {
  it("shows 24-hour time with the short zone name, for example 14:05 GMT+9 in Seoul", () => {
    const instant = Date.UTC(2026, 10, 15, 5, 5); // 05:05Z = 14:05 in Seoul (UTC+9)
    expect(formatStaleSince(instant, en("Asia/Seoul"))).toBe("14:05 GMT+9");
  });

  it("uses the zone's own short name where Intl has one", () => {
    expect(formatStaleSince(Date.UTC(2026, 10, 15, 14, 5), en("UTC"))).toBe("14:05 UTC");
  });

  it("follows daylight saving time in the zone name", () => {
    // 14:05 in New York: 19:05Z in November (EST, UTC-5) and 18:05Z in July (EDT, UTC-4).
    expect(formatStaleSince(Date.UTC(2026, 10, 15, 19, 5), en("America/New_York"))).toBe("14:05 EST");
    expect(formatStaleSince(Date.UTC(2026, 6, 15, 18, 5), en("America/New_York"))).toBe("14:05 EDT");
  });

  it("handles a zone with a half-hour offset", () => {
    const instant = Date.UTC(2026, 10, 15, 8, 35); // 08:35Z = 14:05 in Kolkata (UTC+5:30)
    expect(formatStaleSince(instant, en("Asia/Kolkata"))).toBe("14:05 GMT+5:30");
  });

  it("shows midnight as 00:05, not 24:05", () => {
    const instant = Date.UTC(2026, 10, 14, 15, 5); // 15:05Z on the 14th = 00:05 on the 15th in Seoul
    expect(formatStaleSince(instant, en("Asia/Seoul"))).toBe("00:05 GMT+9");
  });

  it("shows the last minute of the day as 23:59", () => {
    expect(formatStaleSince(Date.UTC(2026, 10, 15, 23, 59), en("UTC"))).toBe("23:59 UTC");
  });

  it("pads hours and minutes with a zero", () => {
    const instant = Date.UTC(2026, 10, 15, 0, 7); // 00:07Z = 09:07 in Seoul
    expect(formatStaleSince(instant, en("Asia/Seoul"))).toBe("09:07 GMT+9");
  });

  it("ignores seconds and milliseconds", () => {
    const instant = Date.UTC(2026, 10, 15, 5, 5, 59, 999);
    expect(formatStaleSince(instant, en("Asia/Seoul"))).toBe("14:05 GMT+9");
  });

  it("works without options, using the viewer's own zone", () => {
    // The text depends on the machine, so this only checks that something with a time separator comes back.
    expect(formatStaleSince(Date.UTC(2026, 10, 15, 5, 5))).toContain(":");
  });
});
