import type { ServiceStatus, StateEvent } from "../api/types";

interface EventLogProps {
  events: StateEvent[];
  /**
   * The current service list. An event whose `serviceId` is in it shows the service name.
   * Any other event (for example from a removed service) shows its raw `serviceId`.
   */
  services?: Pick<ServiceStatus, "id" | "name">[];
}

export function EventLog({ events, services = [] }: EventLogProps) {
  if (events.length === 0) return <p className="muted">No state changes.</p>;
  const names = new Map(services.map((service) => [service.id, service.name]));
  return (
    <ol className="events">
      {events.map((e, index) => (
        <li key={`${index}-${e.serviceId}-${e.at}-${e.to}`}>
          <time dateTime={e.at}>{e.at.slice(11, 16)}Z</time>{" "}
          <span className="event-service">{names.get(e.serviceId) ?? e.serviceId}</span>{" "}
          {e.from} → {e.to}
          <span className="muted"> ({e.note})</span>
        </li>
      ))}
    </ol>
  );
}
