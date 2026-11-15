import type { ServiceStatus } from "../api/types";

const PLACEHOLDER = "—";

/** One block per service, in the order the API returned them. Status is shown as text, not by color alone. */
export function ServiceList({ services }: { services: ServiceStatus[] }) {
  return (
    <ul className="services" aria-live="polite">
      {services.map((service) => (
        <li key={service.id}>
          <dl className="stats">
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
        </li>
      ))}
    </ul>
  );
}
