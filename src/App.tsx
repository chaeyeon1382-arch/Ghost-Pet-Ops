import { useMemo, useState } from "react";
import { buildMockResponse } from "./api/mockStatus";
import { EventLog } from "./components/EventLog";
import { Pet } from "./components/Pet";
import type { PetState } from "./domain/types";

const STATES: PetState[] = ["healthy", "slow", "down", "recovering"];

export function App() {
  const [scenario, setScenario] = useState<PetState>("healthy");
  // Later: replace with a fetch to the API Gateway /status endpoint.
  const data = useMemo(() => buildMockResponse(scenario), [scenario]);
  const service = data.services[0];

  return (
    <main className="screen">
      <h1>GHOST PET OPS</h1>
      <Pet state={service.status} />
      <dl className="stats" aria-live="polite">
        <dt>Service</dt><dd>{service.name}</dd>
        <dt>Status</dt><dd className={`status status--${service.status}`}>{service.status.toUpperCase()}</dd>
        <dt>Response</dt><dd>{service.responseMs} ms</dd>
        <dt>Checked</dt><dd><time dateTime={service.lastChecked}>{service.lastChecked}</time></dd>
      </dl>
      <section>
        <h2>Events</h2>
        <EventLog events={data.events} />
      </section>
      <section className="demo" aria-label="Demo controls">
        <h2>Demo (mock data)</h2>
        {STATES.map((s) => (
          <button key={s} type="button" aria-pressed={scenario === s} onClick={() => setScenario(s)}>
            {s}
          </button>
        ))}
      </section>
    </main>
  );
}
