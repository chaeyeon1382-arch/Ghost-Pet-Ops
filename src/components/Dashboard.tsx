import type { ReactNode } from "react";
import type { StatusView } from "../api/types";
import { worstStatus } from "../domain/worstStatus";
import type { TimeOptions } from "../format/staleSince";
import { EventLog } from "./EventLog";
import { NoSignal } from "./NoSignal";
import { Pet } from "./Pet";
import { ServiceList } from "./ServiceList";

interface DashboardProps {
  view: StatusView;
  /** Extra content under the events, for example the demo buttons in mock mode. */
  children?: ReactNode;
  /** Lets tests fix the time zone and locale of "stale since". */
  timeOptions?: TimeOptions;
}

/**
 * Presentational. It shows what the view says and fetches nothing.
 * The pet shows the worst status among the services, and every service keeps its own row.
 */
export function Dashboard({ view, children, timeOptions }: DashboardProps) {
  const data = view.kind === "loading" ? null : view.data;
  const noSignal = view.kind === "no-signal";
  const frameClass = ["frame", noSignal ? "frame--stale" : "", noSignal && data === null ? "frame--empty" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <main className="screen">
      <h1>GHOST PET OPS</h1>
      <div className={frameClass}>
        {data !== null && (
          <>
            <Pet status={worstStatus(data.services.map((service) => service.status))} />
            <ServiceList services={data.services} />
          </>
        )}
        {view.kind === "loading" && (
          <p className="muted" role="status">
            Loading…
          </p>
        )}
        {view.kind === "no-signal" && <NoSignal staleSinceMs={view.staleSinceMs} timeOptions={timeOptions} />}
      </div>
      {data !== null && (
        <section>
          <h2>Events</h2>
          <EventLog events={data.events} services={data.services} />
        </section>
      )}
      {children}
    </main>
  );
}
