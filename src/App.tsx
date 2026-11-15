import { useMemo, useState } from "react";
import { readConfig } from "./api/config";
import { buildMockResponse } from "./api/mockStatus";
import type { StatusView } from "./api/types";
import { Dashboard } from "./components/Dashboard";
import type { Status } from "./domain/types";
import { useStatusPolling } from "./hooks/useStatusPolling";

const STATUSES: Status[] = ["unknown", "healthy", "slow", "down", "recovering"];

// Chosen at build time. The default is the mock data, so the app works without a backend.
const config = readConfig(import.meta.env);

export function App() {
  return config.dataSource === "api" ? <ApiApp /> : <MockApp />;
}

/** Real data: GET /status, polled. No demo buttons. */
function ApiApp() {
  const view = useStatusPolling(config.baseIntervalMs);
  return <Dashboard view={view} />;
}

/** Mock data with the demo buttons. No network request and no error state. */
function MockApp() {
  const [scenario, setScenario] = useState<Status>("unknown");
  const view = useMemo<StatusView>(() => ({ kind: "live", data: buildMockResponse(scenario) }), [scenario]);

  return (
    <Dashboard view={view}>
      <section className="demo" aria-label="Demo controls">
        <h2>Demo (mock data)</h2>
        {STATUSES.map((s) => (
          <button key={s} type="button" aria-pressed={scenario === s} onClick={() => setScenario(s)}>
            {s}
          </button>
        ))}
      </section>
    </Dashboard>
  );
}
