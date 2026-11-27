import { formatStaleSince, type TimeOptions } from "../format/staleSince";

interface NoSignalProps {
  /** When the last good data arrived. null when there never was any: then no "stale since" line is shown. */
  staleSinceMs: number | null;
  /** Lets tests fix the time zone and locale. Default: the viewer's own. */
  timeOptions?: TimeOptions;
}

/** The "no signal" overlay. It is a UI state, not a pet status. */
export function NoSignal({ staleSinceMs, timeOptions }: NoSignalProps) {
  return (
    <div className="no-signal" role="status">
      <p className="no-signal__title">NO SIGNAL</p>
      <p>Can't reach the ghost. Retrying…</p>
      {staleSinceMs !== null && <p>stale since {formatStaleSince(staleSinceMs, timeOptions)}</p>}
    </div>
  );
}
