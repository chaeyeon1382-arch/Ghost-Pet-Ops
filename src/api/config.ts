/** Build-time configuration. Values come from `VITE_*` variables, which Vite inlines at build time. */

export const DEFAULT_POLL_MS = 10_000;
export const MIN_POLL_MS = 5_000;
export const MAX_POLL_MS = 10_000;
/** Longest wait after a 429, whatever `Retry-After` says. */
export const MAX_BACKOFF_MS = 60_000;

export type DataSource = "mock" | "api";

export interface AppConfig {
  dataSource: DataSource;
  baseIntervalMs: number;
}

/** Only the exact string "api" selects the real API. Anything else keeps the mock data. */
export function resolveDataSource(raw: unknown): DataSource {
  return raw === "api" ? "api" : "mock";
}

/** A finite number or numeric string, limited to [MIN_POLL_MS, MAX_POLL_MS]. Anything else gives the default. */
export function resolvePollInterval(raw: unknown): number {
  let n: number;
  if (typeof raw === "number") {
    n = raw;
  } else if (typeof raw === "string" && raw.trim() !== "") {
    n = Number(raw);
  } else {
    return DEFAULT_POLL_MS;
  }
  if (!Number.isFinite(n)) return DEFAULT_POLL_MS;
  return Math.min(MAX_POLL_MS, Math.max(MIN_POLL_MS, n));
}

export function readConfig(env: Record<string, unknown>): AppConfig {
  return {
    dataSource: resolveDataSource(env.VITE_DATA_SOURCE),
    baseIntervalMs: resolvePollInterval(env.VITE_POLL_INTERVAL_MS),
  };
}
