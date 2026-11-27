# Requirements: Frontend connection to GET /status

## Introduction
This spec connects the React frontend to the `GET /status` API built in `state-store-read-api`.
It implements requirement 11 of that spec (error state, stale marker, 429 backoff, unknown `serviceId`) and design section 5.3.

In scope: a build-time switch between the real API and the existing mock data, polling, response validation, the error state, and Vitest tests.

Out of scope: any change to `backend/`, to `.github/workflows/ci.yml`, or to `src/domain/petState.ts`. No changes to the existing mock data builder (`buildMockResponse`, `buildResponseFromChecks`, `DEMO_SCENARIOS`).
Nothing is deployed and no AWS resources are created. The CloudFront `/status` behavior belongs to the later frontend hosting spec.
**Failure injection is also out of scope.** In `api` mode the demo buttons stay hidden. A real failure-injection control for `api` mode (a way to make the real API fail on purpose) is a follow-up for the failure-injection spec, not part of this one.

The steering documents in `.kiro/steering/` apply, in particular `api-format.md`, `structure.md` and the dependency policy in `tech.md`.

## Glossary
- **Status**: one of `unknown`, `healthy`, `slow`, `down`, `recovering` (lowercase).
- **Data source**: where the app gets a `StatusResponse` from. Either `mock` or `api`.
- **Last good data**: the most recent `StatusResponse` that passed validation.
- **Error state**: a UI state shown after 2 consecutive failed requests. It is not a Status. Also called the "no signal" state.
- **Consecutive failures**: the number of failed requests since the last valid response.
- **Worst status**: the most severe status among the services, using the severity order in Requirement 6.
- **Stale since**: the time the last good data was received.
- **Base interval**: the configured polling interval.
- **Current interval**: the interval actually in use. It is longer than the base interval while backing off.

## Requirement 1: Data source selection
**User story:** As a developer, I want to choose the real API or the mock data at build time so that the app keeps working without a backend.

Acceptance criteria:
1. The data source SHALL be selected by a Vite build-time variable (`VITE_DATA_SOURCE`) with the values `mock` and `api`.
2. WHEN the variable is unset, empty, or has any other value THEN the app SHALL use `mock`.
3. WHEN the source is `mock` THEN the app SHALL use `buildMockResponse`, show the demo buttons, make no network request, and show no error state. Its data and controls are the same as today. Event rows gain a service label (Requirement 7).
4. WHEN the source is `api` THEN the app SHALL read from the relative path `/status` and SHALL NOT show the demo buttons. A failure-injection control for `api` mode is a follow-up for the failure-injection spec.
5. The `/status` path SHALL be relative so that it works behind the same origin (design 6.3, option B). The frontend SHALL NOT contain an API host, account ID, or secret.
6. Components SHALL stay presentational (`structure.md`). Swapping the data source SHALL NOT change what a component receives. Existing component props SHALL keep working. New props SHALL be optional or SHALL be added to components this spec creates.

## Requirement 2: Loading and polling
**User story:** As a viewer, I want the pet to update on its own so that I don't have to reload the page.

Acceptance criteria:
1. WHEN the source is `api` THEN the app SHALL request `/status` once on load.
2. After each completed request, the app SHALL schedule the next request after the current interval. Requests SHALL NOT overlap.
3. The base interval SHALL be configurable at build time (`VITE_POLL_INTERVAL_MS`), SHALL default to 10 000 ms, and SHALL be limited to 5 000–10 000 ms. A value outside that range SHALL be clamped. A value that is not a number SHALL fall back to the default.
4. WHILE the browser tab is hidden THEN the app SHALL NOT start new requests and SHALL NOT keep a pending timer.
5. WHEN the tab becomes visible again THEN the app SHALL request `/status` immediately, unless a request is already in flight.
6. WHEN the component unmounts THEN the app SHALL abort any in-flight request, clear any timer, and remove its `visibilitychange` listener. A response that arrives after unmount SHALL NOT update state.
7. React StrictMode double-mounting in development SHALL NOT leave two polling loops running.
8. Before the first response, the app SHALL show a loading state that is not a Status and does not show the pet as `unknown`.
9. The polling logic SHALL live in a controller that does not depend on React, with `start()` and `stop()`.
10. `start()` SHALL be idempotent: calling it while the controller is running SHALL NOT start a second loop, send a second request, or add a second `visibilitychange` listener.
11. `stop()` SHALL be safe to call any number of times, including before `start()` and twice in a row. A stopped controller MAY be started again and SHALL then run exactly one loop.
12. After `stop()`, no request SHALL be sent, no timer SHALL fire, and no state update SHALL be published, including for a response that was already in flight.

## Requirement 3: Response validation at the boundary
**User story:** As a viewer, I want a malformed response to be rejected so that the pet never shows wrong data.

Acceptance criteria:
1. The app SHALL validate every `/status` response before it reaches any component. Validation SHALL be a pure function with no I/O.
2. A valid response SHALL satisfy `api-format.md`:
   - the body is an object with `services` (array) and `events` (array),
   - `services` contains at least one entry, and service `id` values are unique,
   - each service has string `id` and `name`, `status` as one of the five lowercase values, `responseMs` as a non-negative integer or `null`, and `lastChecked` as a UTC ISO 8601 string with a `Z` suffix or `null`,
   - each event has string `serviceId` and `note`, `from` and `to` as one of the five lowercase values, and `at` as a UTC ISO 8601 string with a `Z` suffix,
   - `events` has at most 20 entries (`MAX_EVENTS`).
   The backend sends timestamps as `YYYY-MM-DDTHH:MM:SSZ`. The validator SHALL also accept an optional fractional-seconds part (`.000Z`), because the mock builder produces it. The value SHALL also be a real calendar date and time.
3. Uppercase or mixed-case status values (`"Healthy"`) SHALL be rejected, not normalized.
4. WHEN a service has `status` `unknown` THEN `responseMs` and `lastChecked` MAY be `null`. For other statuses they MAY also be `null`, because the format allows null before the first check.
5. Unknown extra fields SHALL be ignored, because `api-format.md` allows adding fields. Ignored fields SHALL NOT be passed to components.
6. WHEN validation fails THEN the response SHALL be treated as a request error (Requirement 4) and SHALL NOT be rendered. It SHALL NOT replace the last good data.
7. Validation SHALL NOT throw out of the data layer. It SHALL return either valid data or an error value that names the failing field and does not include the response body.
8. Validation SHALL NOT trust the order of events. The UI SHALL show events in the order received, as the format defines them as newest first.

## Requirement 4: Error state (requirement 11 of state-store-read-api)
**User story:** As a viewer, I want to know when the data is out of date so that I don't trust a stale pet.

Acceptance criteria:
1. A request SHALL count as failed WHEN any of the following happens: a network error, a non-2xx status (including 429 and 5xx), a body that is not valid JSON, or a body that fails validation.
2. The app SHALL NOT rely on the body of a non-2xx response. API Gateway answers 429 with its own body, not the `api-format.md` error format.
3. The "no signal" overlay SHALL appear only after 2 consecutive failures (a named constant, `FAILURES_BEFORE_NO_SIGNAL = 2`). WHILE there has been only 1 failure since the last valid response THEN the last good data SHALL stay fully visible, with no overlay and no dimming. The retry timing rules in Requirement 5 apply from the first failure.
4. WHEN there have been 2 or more consecutive failures and last good data exists THEN the app SHALL keep the last good data visible, dimmed, and SHALL show the overlay with the caption "Can't reach the ghost. Retrying…" and the text "stale since HH:MM".
5. "stale since" SHALL show the time the last good data was received, in the viewer's local time, 24-hour format, followed by the short time zone name from `Intl`, for example `14:05 GMT+9`. The time SHALL NOT change while failures continue. The formatting SHALL be a pure function that accepts an optional time zone and locale, so tests do not depend on the machine's zone.
6. WHEN no good data exists yet THEN the app SHALL keep the loading state after 1 failure. After 2 consecutive failures it SHALL show the overlay and caption without a "stale since" text and without a pet. It SHALL NOT show the pet as any of the five statuses.
7. The error state SHALL be a UI state. The `Status` type and the API status enum SHALL NOT change.
8. The overlay SHALL be exposed to assistive technology (`role="status"` or equivalent), and the dimming SHALL NOT remove the data from the page.
9. WHEN a request succeeds and passes validation THEN the app SHALL replace the data, reset the consecutive failure count to 0, remove the overlay and dimming if shown, and reset the interval to the base interval. No user action SHALL be needed.
10. An aborted request (unmount or tab hidden) SHALL NOT be counted as a failure and SHALL NOT change the failure count.

## Requirement 5: Backoff after 429
**User story:** As an operator, I want clients to slow down when throttled so that viewers don't make the throttling worse.

Acceptance criteria:
1. WHEN the response is 429 AND a valid `Retry-After` header is present THEN the next request SHALL wait for that delay. `Retry-After` in seconds and as an HTTP date SHALL both be accepted.
2. WHEN the response is 429 AND `Retry-After` is absent or invalid THEN the current interval SHALL double.
3. The delay after a 429 SHALL NOT be shorter than the base interval and SHALL NOT exceed 60 000 ms, including when `Retry-After` is larger.
4. WHEN consecutive 429 responses come without `Retry-After` THEN the interval SHALL keep doubling until it reaches 60 000 ms.
5. WHEN a response is a non-429 failure (network error, 5xx, invalid JSON, validation failure) THEN the next request SHALL use the base interval, as in design 5.3.
6. WHEN a request succeeds THEN the current interval SHALL return to the base interval.
7. The backoff and interval calculations SHALL be pure functions that take the previous interval, the outcome, the header value, and the current time as an argument (needed only to read a `Retry-After` HTTP date), and return the next delay. They SHALL NOT read the clock or use timers internally.


## Requirement 6: Several services
**User story:** As a viewer, I want the pet to reflect the whole system, and I still want to see each service's own status.

Current behavior: `App.tsx` reads only `services[0]`. The pet, the status line, the response time and the checked time all come from that one entry, and any other service is ignored. The mock data has one service, so this was never visible. The real API returns every configured service (backend requirement 6.3), so the UI needs a rule.

Acceptance criteria:
1. The pet SHALL show the **worst status** among all services. The severity order, from worst to best, is: `down`, `slow`, `recovering`, `unknown`, `healthy`.
2. The worst-status calculation SHALL be a pure function. For one service it SHALL return that service's status. For an empty list it SHALL NOT be called, because validation rejects an empty list.
3. The worst status SHALL NOT be sent back to the data layer or stored. It is derived for display only. The API status enum SHALL NOT change.
4. Every service SHALL be listed with its own `name`, `status`, response time and last checked time. The status SHALL be shown as text, not by color alone.
5. Services SHALL be shown in the order the API returns them.
6. The severity order SHALL be a named constant defined in one place. It does not change the decision logic in `petState.ts`.

## Requirement 7: Events and unknown services
**User story:** As a viewer, I want to see every recent event, including events of removed services, so that history is not silently hidden.

Acceptance criteria:
1. Each event SHALL show a service label: the service `name` WHEN its `serviceId` is in the current `services` list, otherwise the raw `serviceId`.
2. An event whose `serviceId` is not in the list SHALL still be shown. The event log SHALL NOT crash or drop it.
3. Events SHALL be shown in the order received (newest first).
4. Text from the API (`note`, `serviceId`, `name`) SHALL be rendered as text only. The app SHALL NOT insert it as HTML.
5. The service list the event log receives is optional. WHEN it is not given THEN the label is the raw `serviceId`.

## Requirement 8: Missing values
1. WHEN a service's `responseMs` is `null` THEN its Response field SHALL show "—".
2. WHEN a service's `lastChecked` is `null` THEN its Checked field SHALL show "—".
3. This behavior already exists for one service. It SHALL be kept for every service row and for both data sources.

## Requirement 9: Unchanged code and steering
1. `src/domain/petState.ts` and `src/domain/types.ts` SHALL NOT change.
2. The existing mock data builder in `src/api/mockStatus.ts` SHALL NOT change, and its existing tests SHALL keep passing unchanged.
3. `backend/` and `.github/workflows/ci.yml` SHALL NOT change.
4. `src/api/types.ts` MAY be extended with new types. Existing exported types SHALL NOT be renamed or removed.
5. `vite.config.ts` MAY change in two ways only: the Vitest `include` pattern SHALL cover `.test.tsx` as well as `.test.ts`, and a dev-only `/status` proxy or fake server MAY be added (Requirement 10.5).
6. `.kiro/steering/api-format.md` SHALL get one clarifying sentence in its Errors section: the frontend must not depend on the body of non-2xx responses, because API Gateway and CloudFront can return their own bodies. No other part of that file changes.
7. `.kiro/steering/structure.md` MAY be updated to list the new files.

## Requirement 10: Dependencies, configuration and local development
1. The spec SHALL NOT add a data-fetching library. It SHALL use `fetch`, `AbortController`, `document.visibilityState` and React hooks. design.md SHALL explain why this is enough.
2. The spec SHALL NOT add a runtime or dev dependency. The rendering test SHALL use `react-dom/server`, which ships with React.
3. Build-time variables SHALL contain no secrets. They SHALL be documented in the README, not in a `.env.example`, because `.gitignore` excludes `.env.*`. Real `.env*` files stay ignored.
4. The default build (no variables set) SHALL work with `npm run build` and SHALL use the mock source.
5. design.md SHALL describe how a developer runs `api` mode locally without a deployed backend and without a new dependency (a Vite dev proxy and a small dev-only fake `/status`). Dev-only settings SHALL NOT be exposed to browser code and SHALL NOT affect production builds.

## Requirement 11: Tests
**User story:** As a developer, I want tests that run offline so that CI never depends on a backend.

Acceptance criteria:
1. All new Vitest tests SHALL run with `npm test` without network access. `fetch` SHALL be replaced by a fake in tests. Real timers SHALL NOT be used for waiting. Fake timers SHALL be used where time matters.
2. Response validation SHALL be covered:
   - a valid response, including `unknown` with `null` `responseMs` and `lastChecked`, and a timestamp with and without fractional seconds,
   - uppercase status, a sixth status value, a missing field, wrong types, a non-`Z` timestamp, an impossible date, negative or fractional `responseMs`, more than 20 events, an empty `services` list, duplicate service ids, and a non-object body,
   - extra fields being dropped.
3. Polling and backoff SHALL be covered as pure functions and with fake timers:
   - the interval clamp and default,
   - `Retry-After` in seconds and as a date, invalid values, the 60 s cap, and the base interval floor,
   - doubling without `Retry-After`, reset after success, and the base interval after non-429 failures.
4. The stale and recovery transitions SHALL be covered: 1 failure keeps the data fully visible, the 2nd consecutive failure triggers the error state, repeated failures keep the original "stale since" time, a success clears the error state and the failure count, a failure before any good data (loading after 1, overlay after 2), and an aborted request not counting as a failure.
5. The controller SHALL be covered: the first request on `start()`, the next request after the interval, no overlapping requests, `start()` twice running one loop, `stop()` safe to call repeatedly and before `start()`, restart after `stop()` running one loop, unmount aborting the in-flight request and clearing the timer, no state update after `stop()`, and hidden-tab pause and resume.
6. The worst-status function SHALL be covered for the whole severity order, one service, and ties.
7. The "stale since" formatter SHALL be covered with fixed time zones (for example `Asia/Seoul` giving `14:05 GMT+9`), including midnight (`00:05`, not `24:05`).
8. Rendering tests (with `react-dom/server`) SHALL check: the overlay caption, "stale since" with the time zone name, the dimmed data still present, no "stale since" and no pet before any good data, no overlay after a single failure, the worst status driving the pet while each service keeps its own status, and an event with an unknown `serviceId` showing its raw id.
9. The existing tests SHALL keep passing unchanged.
10. IF the tests cannot run in the development sandbox THEN the final report SHALL say so and SHALL NOT claim they passed. The pull request CI result is then the evidence.

## Decisions (approved)
1. **Default interval:** 10 s. The range stays 5–10 s.
2. **"stale since":** viewer's local time, 24-hour, with the short zone name from `Intl` (for example `14:05 GMT+9`).
3. **First load fails:** the overlay with no pet and no "stale since", after 2 consecutive failures.
4. **Rendering tests:** `react-dom/server` plus a `.test.tsx` include pattern. The polling controller is a plain class with an idempotent `start()` and a repeat-safe `stop()`, and this is tested.
5. **`api-format.md`:** one clarifying sentence about non-2xx bodies (Requirement 9.6). It is added during implementation, not before the tasks are approved.
6. **Overlay threshold:** the overlay appears only after 2 consecutive failures.
7. **Several services:** the pet shows the worst status. Every service stays visible.
8. **Failure injection for `api` mode:** a follow-up for the failure-injection spec.
