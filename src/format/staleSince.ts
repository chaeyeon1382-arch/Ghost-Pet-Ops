/**
 * Formats the time the last good data was received, for the "stale since" text.
 * Result: "HH:MM <zone>" in 24-hour time with the short zone name from Intl, for example "14:05 GMT+9".
 * Without options it uses the viewer's own time zone and locale. Tests pass both explicitly.
 * Pure: the time is an argument and nothing reads the clock.
 */
export function formatStaleSince(ms: number, opts: { timeZone?: string; locale?: string } = {}): string {
  const parts = new Intl.DateTimeFormat(opts.locale, {
    hour: "2-digit",
    minute: "2-digit",
    // h23 gives 00:05 at midnight. hour12: false can give 24:05 in some engines.
    hourCycle: "h23",
    timeZoneName: "short",
    timeZone: opts.timeZone,
  }).formatToParts(new Date(ms));

  // Reading the parts avoids depending on a locale's punctuation or ordering.
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("hour")}:${part("minute")} ${part("timeZoneName")}`;
}
