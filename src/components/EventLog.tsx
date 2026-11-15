import type { StateEvent } from "../api/types";

export function EventLog({ events }: { events: StateEvent[] }) {
  if (events.length === 0) return <p className="muted">No state changes.</p>;
  return (
    <ol className="events">
      {events.map((e) => (
        <li key={`${e.serviceId}-${e.at}-${e.to}`}>
          <time dateTime={e.at}>{e.at.slice(11, 16)}Z</time> {e.from} → {e.to}
          <span className="muted"> ({e.note})</span>
        </li>
      ))}
    </ol>
  );
}
