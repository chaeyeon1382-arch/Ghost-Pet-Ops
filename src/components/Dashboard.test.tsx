import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { applyOutcome, initialPollState, toView } from "../api/statusState";
import type { FetchOutcome, ServiceStatus, StateEvent, StatusResponse, StatusView } from "../api/types";
import { Dashboard } from "./Dashboard";
import { EventLog } from "./EventLog";

// Server rendering only: no browser, no effects, no network. Fixtures are fixed, and the time zone and
// locale of "stale since" are passed in, so nothing depends on the machine.
const SEOUL = { timeZone: "Asia/Seoul", locale: "en-US" };
const STALE_AT = Date.UTC(2026, 10, 15, 5, 5); // 05:05Z = 14:05 in Seoul

const HEALTHY: ServiceStatus = {
  id: "site-1",
  name: "Example",
  status: "healthy",
  responseMs: 84,
  lastChecked: "2026-11-15T02:00:00Z",
};
const DOWN: ServiceStatus = {
  id: "site-2",
  name: "Blog",
  status: "down",
  responseMs: 5000,
  lastChecked: "2026-11-15T02:01:00Z",
};
const UNCHECKED: ServiceStatus = { id: "site-3", name: "Docs", status: "unknown", responseMs: null, lastChecked: null };

const EVENT_DOWN: StateEvent = {
  serviceId: "site-2",
  from: "healthy",
  to: "down",
  at: "2026-11-15T01:58:00Z",
  note: "3 failures in a row",
};
const EVENT_REMOVED: StateEvent = {
  serviceId: "removed-site",
  from: "slow",
  to: "healthy",
  at: "2026-11-15T01:50:00Z",
  note: "old event",
};

function response(services: ServiceStatus[], events: StateEvent[] = []): StatusResponse {
  return { services, events };
}

const live = (data: StatusResponse): StatusView => ({ kind: "live", data });

/** Decodes the few entities React writes, so text can be compared as people read it. */
const decode = (html: string) =>
  html
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");

function renderRaw(view: StatusView, children?: ReactNode) {
  return renderToStaticMarkup(
    <Dashboard view={view} timeOptions={SEOUL}>
      {children}
    </Dashboard>,
  );
}

const render = (view: StatusView, children?: ReactNode) => decode(renderRaw(view, children));

const success = (data: StatusResponse): FetchOutcome => ({ kind: "success", data });
const failure: FetchOutcome = { kind: "failure", reason: { kind: "network" }, retryAfter: null };

/** A view built by the real state functions, from outcomes in order. */
function viewAfter(outcomes: FetchOutcome[]): StatusView {
  const state = outcomes.reduce((s, outcome) => applyOutcome(s, outcome, STALE_AT), initialPollState);
  return toView(state);
}

describe("Dashboard: loading", () => {
  it("shows a loading message and no pet, no events and no overlay", () => {
    const html = render({ kind: "loading" });
    expect(html).toContain("Loading…");
    expect(html).not.toContain('class="pet');
    expect(html).not.toContain("Events");
    expect(html).not.toContain("NO SIGNAL");
    expect(html).not.toContain("frame--stale");
  });

  it("is also the view after a single failure with no data", () => {
    expect(viewAfter([failure])).toEqual({ kind: "loading" });
  });
});

describe("Dashboard: live data", () => {
  it("shows the pet, the service and the events, without overlay or dimming", () => {
    const html = render(live(response([HEALTHY], [EVENT_DOWN])));
    expect(html).toContain("pet--healthy");
    expect(html).toContain("Example");
    expect(html).toContain("HEALTHY");
    expect(html).toContain("84 ms");
    expect(html).toContain('class="frame"');
    expect(html).not.toContain("NO SIGNAL");
    expect(html).not.toContain("stale since");
    expect(html).not.toContain("frame--stale");
  });

  it("says when there are no events", () => {
    expect(render(live(response([HEALTHY])))).toContain("No state changes.");
  });

  it("shows the service's checked time as a time element", () => {
    const html = render(live(response([HEALTHY])));
    // React may write the dateTime prop as "datetime", so the attribute name is matched case-insensitively.
    expect(html).toMatch(/<time datetime="2026-11-15T02:00:00Z">2026-11-15T02:00:00Z<\/time>/i);
  });
});

describe("Dashboard: one failure keeps the data fully visible", () => {
  it("shows no overlay and no dimming after a single failure", () => {
    const view = viewAfter([success(response([HEALTHY])), failure]);
    expect(view.kind).toBe("live");
    const html = render(view);
    expect(html).toContain("pet--healthy");
    expect(html).toContain("Example");
    expect(html).not.toContain("NO SIGNAL");
    expect(html).not.toContain("frame--stale");
  });
});

describe("Dashboard: no signal with data", () => {
  const view = viewAfter([success(response([HEALTHY], [EVENT_DOWN])), failure, failure]);

  it("is the view after two consecutive failures", () => {
    expect(view.kind).toBe("no-signal");
  });

  it("shows the overlay, the caption and the stale time with the zone name", () => {
    const html = render(view);
    expect(html).toContain("NO SIGNAL");
    expect(html).toContain("Can't reach the ghost. Retrying…");
    expect(html).toContain("stale since 14:05 GMT+9");
  });

  it("exposes the overlay to assistive technology", () => {
    const html = render(view);
    expect(html).toContain('class="no-signal"');
    expect(html).toContain('role="status"');
  });

  it("dims the frame but keeps the last good data in the page", () => {
    const html = render(view);
    expect(html).toContain('class="frame frame--stale"');
    expect(html).toContain("pet--healthy");
    expect(html).toContain("Example");
    expect(html).toContain("HEALTHY");
    expect(html).toContain("3 failures in a row");
  });

  it("is not a pet status: the pet still shows the status of the data", () => {
    const html = render(view);
    expect(html).not.toContain("pet--no-signal");
    expect(html).not.toContain("status--no-signal");
  });

  it("keeps the original stale time after more failures", () => {
    const later = viewAfter([success(response([HEALTHY])), failure, failure, failure, failure]);
    expect(render(later)).toContain("stale since 14:05 GMT+9");
  });

  it("goes back to a plain live view after a success", () => {
    const recovered = viewAfter([success(response([HEALTHY])), failure, failure, success(response([HEALTHY]))]);
    const html = render(recovered);
    expect(html).not.toContain("NO SIGNAL");
    expect(html).not.toContain("frame--stale");
  });
});

describe("Dashboard: no signal without data", () => {
  const view = viewAfter([failure, failure]);

  it("is the view after two failures with no good data", () => {
    expect(view).toEqual({ kind: "no-signal", data: null, staleSinceMs: null });
  });

  it("shows the overlay and caption but no stale time", () => {
    const html = render(view);
    expect(html).toContain("NO SIGNAL");
    expect(html).toContain("Can't reach the ghost. Retrying…");
    expect(html).not.toContain("stale since");
  });

  it("shows no pet, no services and no events, so no status is implied", () => {
    const html = render(view);
    expect(html).not.toContain('class="pet');
    expect(html).not.toContain("HEALTHY");
    expect(html).not.toContain("UNKNOWN");
    expect(html).not.toContain("Events");
    expect(html).toContain("frame--empty");
  });
});

describe("Dashboard: several services", () => {
  it("shows the worst status as the pet while each service keeps its own status text", () => {
    const html = render(live(response([HEALTHY, DOWN])));
    expect(html).toContain("pet--down");
    expect(html).not.toContain("pet--healthy");
    expect(html).toContain("Example");
    expect(html).toContain("Blog");
    expect(html).toContain("status--healthy");
    expect(html).toContain("status--down");
    expect(html).toContain("HEALTHY");
    expect(html).toContain("DOWN");
  });

  it("keeps the pet waiting when one service is unknown among healthy ones", () => {
    const html = render(live(response([HEALTHY, UNCHECKED])));
    expect(html).toContain("pet--unknown");
    expect(html).toContain("HEALTHY");
    expect(html).toContain("UNKNOWN");
  });

  it("lists services in the order the API returned them", () => {
    const html = render(live(response([DOWN, HEALTHY])));
    expect(html.indexOf("Blog")).toBeLessThan(html.indexOf("Example"));
  });
});

describe("Dashboard: missing values", () => {
  it("shows a dash for null responseMs and lastChecked, and nothing else in their place", () => {
    const html = render(live(response([UNCHECKED])));
    expect(html.split("—")).toHaveLength(3); // exactly two dashes
    expect(html).not.toContain(" ms");
    expect(html).not.toContain("<time");
    expect(html).toContain("pet--unknown");
  });

  it("shows real values for a service that has them", () => {
    const html = render(live(response([HEALTHY])));
    expect(html).not.toContain("—");
  });
});

describe("Dashboard: events and unknown services", () => {
  const view = live(response([HEALTHY, DOWN], [EVENT_DOWN, EVENT_REMOVED]));

  it("labels an event of a known service with the service name", () => {
    const html = render(view);
    expect(html).toContain('<span class="event-service">Blog</span>');
    expect(html).toContain("healthy → down");
  });

  it("still shows an event of an unknown service, labeled with its raw serviceId", () => {
    const html = render(view);
    expect(html).toContain('<span class="event-service">removed-site</span>');
    expect(html).toContain("slow → healthy");
    expect(html).toContain("old event");
  });

  it("does not show the raw id of a known service", () => {
    expect(render(view)).not.toContain("site-2");
  });

  it("shows the event time in UTC", () => {
    expect(render(view)).toContain("01:58Z");
  });

  it("uses the raw serviceId when no service list is given", () => {
    const html = decode(renderToStaticMarkup(<EventLog events={[EVENT_DOWN]} />));
    expect(html).toContain('<span class="event-service">site-2</span>');
  });
});

describe("Dashboard: text from the API is not HTML", () => {
  it("escapes markup in names and notes", () => {
    const service: ServiceStatus = { ...HEALTHY, name: "<b>Bold</b>" };
    const event: StateEvent = { ...EVENT_DOWN, note: "<img src=x onerror=alert(1)>" };
    const raw = renderRaw(live(response([service], [event])));
    expect(raw).toContain("&lt;b&gt;Bold&lt;/b&gt;");
    expect(raw).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(raw).not.toContain("<b>");
    expect(raw).not.toContain("<img");
  });
});

describe("Dashboard: extra content", () => {
  it("renders children, such as the demo controls, only when they are passed", () => {
    const view = live(response([HEALTHY]));
    expect(render(view, <p>demo-controls</p>)).toContain("demo-controls");
    expect(render(view)).not.toContain("demo-controls");
  });
});
