import { useMemo, useState } from "react";
import { buildMockResponse } from "./api/mockStatus";
import { EventLog } from "./components/EventLog";
import { Pet } from "./components/Pet";
import type { Status } from "./domain/types";

const STATUSES: Status[] = ["unknown", "healthy", "slow", "down", "recovering"];
const PLACEHOLDER = "—";

export function App() {
  const [scenario, setScenario] = useState<Status>("unknown");
  // Later: replace with a fetch to the API Gateway /status endpoint.
  const data = useMemo(() => buildMockResponse(scenario), [scenario]);
  const service = data.services[0];

  return (
    <main className="screen">
      <h1>GHOST PET OPS</h1>
      <Pet status={service.status} />
      <dl className="stats" aria-live="polite">
        <dt>Service</dt>
        <dd>{service.name}</dd>
        <dt>Status</dt>
        <dd className={`status status--${service.status}`}>{service.status.toUpperCase()}</dd>
        <dt>Response</dt>
        <dd>{service.responseMs === null ? PLACEHOLDER : `${service.responseMs} ms`}</dd>
        <dt>Checked</dt>
        <dd>
          {service.lastChecked === null ? (
            PLACEHOLDER
          ) : (
            <time dateTime={service.lastChecked}>{service.lastChecked}</time>
          )}
        </dd>
      </dl>
      <section>
        <h2>Events</h2>
        <EventLog events={data.events} />
      </section>
      <section className="demo" aria-label="Demo controls">
        <h2>Demo (mock data)</h2>
        {STATUSES.map((s) => (
          <button key={s} type="button" aria-pressed={scenario === s} onClick={() => setScenario(s)}>
            {s}
          </button>
        ))}
      </section>
    </main>
  );
}
