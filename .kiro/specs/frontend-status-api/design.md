# Design: Frontend connection to GET /status

Requirement numbers refer to `requirements.md` in this folder. Section 5.3 of `state-store-read-api/design.md` is the starting point.

## 1. Overview

The app gets a `StatusResponse` from one of two sources, chosen at build time:

```
VITE_DATA_SOURCE=mock (default)   App -> buildMockResponse(scenario)  -> StatusView {live}
VITE_DATA_SOURCE=api              App -> useStatusPolling -> PollController -> fetchStatus -> validateStatus
                                                                   |
                                                         applyOutcome (pure) -> StatusView
                                                                   |
                                                       Dashboard (presentational)
```

Everything that decides something is a pure function (validation, interval calculation, state transitions, worst status, time formatting).
The only objects with side effects are `fetchStatus` (network), `PollController` (timers, abort, visibility) and one React hook. The controller does not import React.

## 2. Decision: no data-fetching library (Requirement 10.1)

| | Plain `fetch` + one controller (chosen) | TanStack Query / SWR |
|---|---|---|
| Added runtime code | About 150 lines of our own | A new runtime dependency (`tech.md` requires a justification) |
| Pause when the tab is hidden | `visibilitychange` listener, 10 lines | Built in |
| Cancel on unmount | `AbortController`, 5 lines | Built in |
| `Retry-After` and "double up to 60 s" | Needed either way. The libraries' retry options take a delay function, but we would still write the same pure function. | Same |
| "Overlay after 2 consecutive failures, keep last good data, stale time" | Our reducer, about 30 lines | Possible, but needs custom logic on top of the library's own `error`/`data` states |
| Idempotent `start`/`stop` as a testable unit | Direct | The lifecycle belongs to the library, so Requirement 2.9–2.12 would be tested through React |

There is one endpoint, one consumer and no cache sharing. The library's main benefits do not apply. Plain code also keeps every rule in Requirements 2, 4 and 5 visible and unit-testable. No dependency is added.

## 3. Files

New files are marked `+`. Changed files are marked `~`.

```
src/
  api/
    types.ts                 ~ add FetchOutcome, FailureReason, PollState, StatusView (existing exports untouched)
  + config.ts                  readConfig(env), resolveDataSource, resolvePollInterval, constants
  + validateStatus.ts          validateStatus(body: unknown): ValidationResult  (pure)
  + fetchStatus.ts             fetchStatus(signal, fetchImpl?): Promise<FetchOutcome>  (never rejects)
  + pollSchedule.ts            parseRetryAfter, nextDelay  (pure)
  + statusState.ts             initialPollState, applyOutcome, toView  (pure)
  + pollController.ts          PollController class (no React)
    mockStatus.ts              unchanged
  domain/
  + worstStatus.ts             SEVERITY_ORDER, worstStatus(statuses)  (pure, imports domain/types only)
    petState.ts, types.ts      unchanged
  format/
  + staleSince.ts              formatStaleSince(ms, opts?)  (pure)
  hooks/
  + useStatusPolling.ts        React glue: creates the controller in an effect, returns a StatusView
  components/
  + Dashboard.tsx              presentational: takes a StatusView and optional children (demo controls)
  + ServiceList.tsx            one row per service
  + NoSignal.tsx               the overlay
    EventLog.tsx               ~ optional `services` prop, service label per event
    Pet.tsx                    unchanged
  App.tsx                      ~ picks the source, wires Dashboard
  styles.css                   ~ .frame, .frame--stale, .no-signal, .services
vite.config.ts                 ~ test include .test.tsx; dev-only /status proxy or fake (section 10)
README.md                      ~ build-time variables and local api mode
.kiro/steering/api-format.md   ~ one sentence (section 12)
.kiro/steering/structure.md    ~ list the new files and folders
```

Dependency direction stays as in `structure.md`: `components -> api types, domain`, `hooks -> api, React`, `api -> domain`. `domain` imports nothing outside `domain`. `src/api/` stays React-free.

## 4. Configuration (Requirement 1, 2.3)

`src/api/config.ts`:

```ts
export const DEFAULT_POLL_MS = 10_000;
export const MIN_POLL_MS = 5_000;
export const MAX_POLL_MS = 10_000;
export const MAX_BACKOFF_MS = 60_000;

export type DataSource = "mock" | "api";
export interface AppConfig { dataSource: DataSource; baseIntervalMs: number }

export function resolveDataSource(raw: unknown): DataSource;       // only the exact string "api" gives "api"
export function resolvePollInterval(raw: unknown): number;         // non-numeric -> DEFAULT, else clamp to [MIN, MAX]
export function readConfig(env: Record<string, unknown>): AppConfig;
```

`App.tsx` calls `readConfig(import.meta.env)` once at module level. `VITE_*` values are inlined at build time, so they hold no secrets and nothing is read at runtime. Tests call the functions with plain objects.
`resolvePollInterval` accepts only a finite number or a numeric string (`"7000"`). `""`, `"abc"`, `NaN` and `undefined` give the default. `Infinity` is not finite, so it also gives the default.

## 5. Types (Requirement 3, 4)

Added to `src/api/types.ts`:

```ts
export type FailureReason =
  | { kind: "network" }
  | { kind: "http"; status: number }
  | { kind: "invalid_json" }
  | { kind: "invalid_shape"; field: string };       // field path only, never the body

export type FetchOutcome =
  | { kind: "success"; data: StatusResponse }
  | { kind: "failure"; reason: FailureReason; retryAfter: string | null }  // retryAfter: raw header, only read for 429
  | { kind: "aborted" };

export interface PollState {
  data: StatusResponse | null;      // last good data
  receivedAt: number | null;        // epoch ms of the last good data
  consecutiveFailures: number;
  lastFailure: FailureReason | null;
}

export type StatusView =
  | { kind: "loading" }
  | { kind: "live"; data: StatusResponse }
  | { kind: "no-signal"; data: StatusResponse | null; staleSinceMs: number | null };
```

`no-signal` is a view, not a `Status`. The `Status` type is untouched (Requirement 4.7).

## 6. Validation (Requirement 3)

`validateStatus(body: unknown): { ok: true; data: StatusResponse } | { ok: false; field: string }`

- Walks the body with small type guards. It builds a **new** object from the allowed fields only, so extra fields are dropped (3.5).
- Status check: `typeof v === "string" && STATUSES.includes(v)`. The list of five values is a `readonly` array in `validateStatus.ts`, typed `Status[]`, so the compiler flags it if `Status` ever changes. No normalization: `"Healthy"` fails (3.3).
- Timestamp: regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$` **and** a round trip through `Date` for a real date. For example `2026-02-30T00:00:00Z` matches the regex but fails the date check. The date check compares the parsed year, month, day, hour, minute and second with the string's own digits, because `Date.parse` silently rolls over some invalid values.
- **Why fractional seconds are accepted.** The real backend contract is second precision: `state-store-read-api/design.md` 2.3 fixes the format as `YYYY-MM-DDTHH:MM:SSZ`, and the Lambda only ever emits that. The existing mock builder in `src/api/mockStatus.ts` calls `toISOString()`, which emits milliseconds (`2026-11-15T02:00:00.000Z`). The validator accepts the optional `.fff` part so that data in the mock's shape (for example in tests, or a dev fake built from the mock builder) is also valid. This is a deliberate leniency on input only. It does not change the contract: the backend still sends second precision, and `api-format.md` is not changed for this. The mock builder is not modified to match, because it must stay unchanged (Requirement 9.2).
- `responseMs`: `null` or `Number.isInteger(v) && v >= 0`.
- `services`: non-empty array, unique `id`. `events`: array of at most `MAX_EVENTS`.
- On the first problem it returns the field path (`services[1].status`, `events[3].at`, `body`). It does not echo the value. It never throws, and it handles `null`, arrays and primitives as the body.
- It does not sort or filter events (3.8).

## 7. Fetch (Requirement 2, 4.1, 4.2)

```ts
export const STATUS_PATH = "/status";
export async function fetchStatus(signal: AbortSignal, fetchImpl: typeof fetch = fetch): Promise<FetchOutcome>
```

1. `fetchImpl(STATUS_PATH, { signal, headers: { Accept: "application/json" } })`. The default cache mode is kept, so the 5 s `Cache-Control` from the backend applies.
2. A thrown error: `signal.aborted` gives `aborted`, anything else gives `failure/network`.
3. `!res.ok`: `failure/http` with `res.status` and `res.headers.get("Retry-After")`. **The body is never read**, so API Gateway's 429 body and CloudFront's error pages cannot matter (4.2).
4. Reading JSON fails: `aborted` if the signal is aborted, otherwise `failure/invalid_json`.
5. `validateStatus` fails: `failure/invalid_shape`. Otherwise `success`.

It never rejects, so the controller needs no try/catch around it (a defensive `.catch` is still added, see section 9).

## 8. Interval and backoff (Requirement 2.3, 5)

`src/api/pollSchedule.ts`, pure:

```ts
export function parseRetryAfter(raw: string | null, nowMs: number): number | null;  // delay in ms, or null if absent/invalid
export function nextDelay(args: {
  outcome: FetchOutcome; baseMs: number; currentMs: number; nowMs: number;
}): number;
```

`parseRetryAfter`: digits only (`/^\d+$/`) means seconds, times 1000. Otherwise `Date.parse(raw) - nowMs` (an HTTP date). `NaN` gives `null`. A date in the past gives `0`. Negative numbers and decimals such as `"-5"` or `"1.5"` are invalid and give `null`, because the header allows only whole seconds.

`nextDelay`:

| Outcome | Result |
|---|---|
| `success` | `baseMs` (5.6) |
| `failure` with `http` status 429 and a valid `Retry-After` | `clamp(retryAfterMs, baseMs, MAX_BACKOFF_MS)` (5.1, 5.3) |
| `failure` with 429 and no valid `Retry-After` | `clamp(currentMs * 2, baseMs, MAX_BACKOFF_MS)` (5.2, 5.4) |
| any other `failure` | `baseMs` (5.5) |
| `aborted` | `currentMs` (never used in practice, see section 9) |

`currentMs` is the delay used before this request. After a success it equals `baseMs`, so the first 429 without a header gives `2 × base` (20 s with the default), then 40 s, then 60 s.
A non-429 failure resets to `baseMs`, including between two 429s. This matches design 5.3 ("normal interval after a 5xx").

## 9. State and controller (Requirement 2, 4)

### 9.1 State transitions, pure (`statusState.ts`)

```ts
export const FAILURES_BEFORE_NO_SIGNAL = 2;
export const initialPollState: PollState = { data: null, receivedAt: null, consecutiveFailures: 0, lastFailure: null };
export function applyOutcome(state: PollState, outcome: FetchOutcome, nowMs: number): PollState;
export function toView(state: PollState): StatusView;
```

- `success`: `{ data, receivedAt: nowMs, consecutiveFailures: 0, lastFailure: null }` (4.9).
- `failure`: `consecutiveFailures + 1`, `lastFailure` set, `data` and `receivedAt` **kept**. So "stale since" never moves while failures continue (4.5).
- `aborted`: the same state, unchanged (4.10).

`toView`:

| Condition | View |
|---|---|
| `consecutiveFailures >= 2` | `no-signal` with `data` and `staleSinceMs = receivedAt` (both `null` if there was never good data) |
| `data !== null` | `live` (1 failure keeps this, fully visible, 4.3) |
| otherwise | `loading` (includes 1 failure before any data, 4.6) |

### 9.2 `PollController` (`pollController.ts`)

```ts
export interface PollControllerDeps {
  fetchStatus(signal: AbortSignal): Promise<FetchOutcome>;
  baseIntervalMs: number;
  now(): number;
  visibility: { isHidden(): boolean; subscribe(listener: () => void): () => void };
  onChange(state: PollState): void;
}
export class PollController {
  constructor(deps: PollControllerDeps);
  start(): void;
  stop(): void;
}
```

Fields: `running`, `timer`, `inFlight: AbortController | null`, `currentMs`, `state`, `unsubscribe`. Timers use the global `setTimeout`/`clearTimeout`, so Vitest fake timers control them.

- `start()`: `if (running) return;` set `running`, `currentMs = baseIntervalMs`, subscribe to visibility, then `request()` if not hidden. A second call returns immediately, so there is no second loop, request or listener (2.10).
- `request()`: return if `!running`, hidden, or `inFlight` is set (no overlap, 2.2). Clear any timer. Create an `AbortController`, store it in `inFlight`, call `fetchStatus`. In the handler:
  1. `if (inFlight !== ac) return;` A response from a request that was aborted (stop or hide) is ignored. It publishes nothing (2.12).
  2. `inFlight = null`, `state = applyOutcome(state, outcome, now())`, `onChange(state)`.
  3. `currentMs = nextDelay(...)`. If running and not paused, `timer = setTimeout(() => { timer = null; request(); }, currentMs)`. The timer is scheduled before `onChange` is called, so a listener that throws cannot stop the loop. `onChange` is skipped when the state did not change (an `aborted` outcome).
  A defensive `.catch` turns an unexpected rejection into a `network` failure.
- Visibility listener: hidden sets a `paused` flag, calls `clearTimeout`, aborts `inFlight` and sets it to `null`. Visible calls `request()` immediately only if the controller was paused (2.4, 2.5). A visible event while no pause is active (a repeated or spurious event) does nothing, so it cannot cause an extra request. A visible event while a request is already in flight also does nothing. The aborted request leaves no stale callback, because of check 1 above. `start()` while the page is hidden sets `paused` and sends nothing.
- `stop()`: `running = false`, clear timer, abort and clear `inFlight`, call `unsubscribe` and clear it. Every step is a no-op when already done, so repeated calls and a call before `start()` are safe (2.11). A later `start()` begins a clean single loop and keeps the last state.

The controller never throws and never calls `onChange` after `stop()`.

### 9.3 React glue (`hooks/useStatusPolling.ts`)

```ts
export function useStatusPolling(baseIntervalMs: number): StatusView {
  const [view, setView] = useState<StatusView>({ kind: "loading" });
  useEffect(() => {
    const controller = new PollController({
      fetchStatus: (signal) => fetchStatus(signal),
      baseIntervalMs,
      now: () => Date.now(),
      visibility: documentVisibility,           // wraps document.visibilityState and "visibilitychange"
      onChange: (s) => { setView(toView(s)); if (s.lastFailure) console.warn("[status]", s.lastFailure); },
    });
    controller.start();
    return () => controller.stop();
  }, [baseIntervalMs]);
  return view;
}
```

StrictMode runs the effect, its cleanup, then the effect again. The first controller is stopped (its request aborted, listener removed) before the second starts, so one loop remains (2.7). The warning prints the failure reason (status code or field path, never a body) to help debugging, since the overlay does not show causes.

## 10. Components (Requirement 4, 6, 7, 8)

`App.tsx`:

```tsx
const config = readConfig(import.meta.env);
export function App() { return config.dataSource === "api" ? <ApiApp /> : <MockApp />; }
function ApiApp()  { const view = useStatusPolling(config.baseIntervalMs); return <Dashboard view={view} />; }
function MockApp() { /* scenario state as today; view = { kind: "live", data: buildMockResponse(scenario) } */
                     return <Dashboard view={view}>{demo buttons}</Dashboard>; }
```

The two apps are separate components so the hook is never called conditionally. The mock path keeps `useMemo(buildMockResponse(scenario))` and the existing demo buttons as `children`. In `api` mode no demo buttons are rendered (1.4).

`Dashboard({ view, children })` (presentational, rendered by tests with `renderToStaticMarkup`):

```
<main className="screen">
  <h1>GHOST PET OPS</h1>
  <div className={"frame" + (view.kind === "no-signal" ? " frame--stale" : "")}>
    {data && <><Pet status={worstStatus(data.services.map(s => s.status))} /> <ServiceList services={data.services} /></>}
    {view.kind === "loading" && <p role="status" className="muted">Loading…</p>}
    {view.kind === "no-signal" && <NoSignal staleSinceMs={view.staleSinceMs} />}
  </div>
  {data && <section><h2>Events</h2><EventLog events={data.events} services={data.services} /></section>}
  {children}
</main>
```

- **Dimming** is a CSS class on the frame. The data stays in the DOM and readable by assistive technology (4.8). The overlay is a sibling positioned over the frame, so it is not dimmed itself.
- **No data and no-signal**: the frame shows only the overlay and an empty box. There is no pet and no events, so no status is implied (4.6).
- **`NoSignal`**: `<div role="status" className="no-signal">` containing "NO SIGNAL", "Can't reach the ghost. Retrying…" and, only when `staleSinceMs !== null`, `stale since {formatStaleSince(staleSinceMs)}`.
- **Test hook for the zone and locale.** `Dashboard` and `NoSignal` take an optional `timeOptions?: { timeZone?: string; locale?: string }` prop (type `TimeOptions`, exported from `format/staleSince.ts`) and pass it to `formatStaleSince`. Both default to the viewer's own values. A locale is needed as well as a zone, because the zone *name* (`GMT+9`, `EST`) comes from the locale's data. Rendering tests pass both, so they do not depend on the machine's settings.
- **`ServiceList`**: one `<li>` per service in API order. Each has the name, `STATUS` in text with the existing `status--{status}` class, Response (`"—"` when `null`) and Checked (`"—"` when `null`, otherwise `<time>`). This is the current `<dl>` repeated per service, so the existing `PLACEHOLDER` behavior is kept (Requirement 8).
- **`EventLog`**: new optional prop `services?: { id: string; name: string }[]`. The label is `name` when `serviceId` matches, otherwise the raw `serviceId`. Rows render `<time>` + label + `from → to` + note, all as React text children (no `dangerouslySetInnerHTML`, 7.4). The React `key` becomes `${index}-${serviceId}-${at}-${to}` to stay unique for repeated events. Without the prop the label is the raw `serviceId` (7.5).

  Because event rows now carry a label, the mock UI changes slightly (a service label per row). This is the only visible change in `mock` mode. It is needed because several services can now produce events.

### 10.1 Worst status (`domain/worstStatus.ts`)

```ts
export const SEVERITY_ORDER: readonly Status[] = ["down", "slow", "recovering", "unknown", "healthy"]; // worst first
export function worstStatus(statuses: readonly Status[]): Status;   // first SEVERITY_ORDER entry present
```

This is a new file. `petState.ts` and `types.ts` are not touched (6.6, 9.1). With an empty list it returns `"unknown"` as a defined fallback, and the Dashboard does not call it with an empty list because validation rejects one. The result is only used for display (6.3).

**A service stuck in `unknown` keeps the pet in the waiting look.** In the order above, `unknown` ranks worse than `healthy`. If even one service is `unknown` and all others are `healthy`, `worstStatus` returns `unknown`, and the pet shows its `unknown` art with the caption "Waiting for the first health check...". This is intended, because "healthy" would claim that every service has been checked. What it means in practice:
- **The checker does not exist yet** (`state-store-read-api` design 10). Until it is built and has run, the real API returns every service as `unknown` with `null` values, so in `api` mode the pet will sit in the waiting look and every row will show "—". This is correct behavior, not a bug.
- **Adding a service to `services.json`** makes the pet wait again until the checker has run once for it, even if the other services are healthy.
- **The caption can read oddly** with a mix of states ("Waiting for the first health check..." while two other rows say HEALTHY). The service list is the place to see which one is unknown. The caption text is not changed in this spec.
- **For a demo, use `mock` mode or `DEV_FAKE_STATUS=healthy`** (section 11) until the checker runs against the real table. The demo buttons exist only in `mock` mode.

### 10.2 "stale since" (`format/staleSince.ts`)

```ts
export function formatStaleSince(ms: number, opts?: { timeZone?: string; locale?: string }): string;
```

Uses `Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short", timeZone })` and `formatToParts`, then joins `hour`, `minute` and `timeZoneName` as `HH:MM <zone>`. Reading the parts avoids depending on locale-specific punctuation or order, and `hourCycle: "h23"` gives `00:05` at midnight. Without `opts` it uses the viewer's own zone and locale. Tests pass `{ timeZone: "Asia/Seoul", locale: "en-US" }` and expect `14:05 GMT+9`.

### 10.3 Styles

Added to `src/styles.css` (plain CSS, no framework):
`.frame { position: relative; min-height: … }`, `.frame--stale > :not(.no-signal) { opacity: .35; filter: grayscale(1) }`, `.no-signal { position: absolute; inset: 0; display: grid; place-content: center; text-align: center }`, and `.services` list styles reusing the existing `.stats` look. The exact values are chosen when the file is edited. Dimming relies on opacity plus text, never on color alone.

## 11. Running `api` mode locally (Requirement 10.5)

The browser calls the relative `/status`, so in development something has to answer on the Vite dev server's origin. `vite.config.ts` gets two **dev-only** options, selected by environment variables **without** the `VITE_` prefix, so Vite never exposes them to browser code and production builds ignore them.

| Variable | Effect |
|---|---|
| `DEV_FAKE_STATUS=<healthy\|slow\|down\|recovering\|unknown>` | A small plugin (`apply: "serve"`) answers `GET /status` from the existing `buildMockResponse(scenario)` with `Cache-Control: no-store`. No backend and no new package. |
| `DEV_API_TARGET=<url>` | A normal Vite `server.proxy` entry forwards `/status` to that URL (for example a deployed stack). |

Setting both is an error at config load, so a developer is never unsure which one answered.

### 11.1 The fake plugin runs only in the dev server

- The plugin sets `apply: "serve"`, so Vite uses it only for `vite` / `npm run dev`. It is not applied for `vite build`. Its only hook is `configureServer`, which Vite calls only for a dev server.
- `server.proxy` is a dev-server option. `npm run preview` has its own `preview.proxy`, which this spec does not set, so `preview` serves no `/status` unless a real one sits in front.
- The plugin code lives in `vite.config.ts`, which Vite runs in Node to configure the build. It is not part of the browser bundle.
- **No `src/` code reads `DEV_*` variables.** Browser code reads only `import.meta.env.VITE_DATA_SOURCE` and `import.meta.env.VITE_POLL_INTERVAL_MS`, through `readConfig`. Vite exposes only variables with the `VITE_` prefix to `import.meta.env`, so a `DEV_*` variable could not reach the browser even by mistake.
- The mock builder (`buildMockResponse`) is in the production bundle by design, because `mock` is the default source. So the check below looks for strings that exist only in the dev tooling, not for `buildMockResponse`.

**How we verify it** (a task, run manually, because the CI workflow must not change):
1. `grep -rn "DEV_" src/` finds nothing.
2. `npm run build` with no variables, then again with `VITE_DATA_SOURCE=api`, and once with `DEV_FAKE_STATUS=healthy npm run build`. The last case proves that a stray dev variable does nothing to a production build.
3. In each case, `grep -rE "DEV_FAKE_STATUS|DEV_API_TARGET|fake-status" dist/` finds nothing. The plugin's name and its log/error strings contain `fake-status`, so the search has something to find if the code leaked.
4. `grep -rE "execute-api|amazonaws|localhost" dist/` finds nothing, which shows that no API host or dev address is baked into the bundle. The relative `/status` path may appear, which is expected.
The PR description states that these checks were run, with the commands. CI still runs the normal `npm test` and `npm run build`, which only proves that the default build works.

```
# fake data, no backend
VITE_DATA_SOURCE=api DEV_FAKE_STATUS=healthy npm run dev

# real API
VITE_DATA_SOURCE=api DEV_API_TARGET=https://<host> npm run dev

# see the "no signal" overlay: point at a closed port (the proxy returns an error)
VITE_DATA_SOURCE=api DEV_API_TARGET=http://127.0.0.1:9 npm run dev
```

With the closed port, the first request fails at page load and the second one interval later (10 s), so the overlay appears after about 10 s. That is the 2-failure rule at work: one failure alone shows only "Loading…".

- `vite.config.ts` becomes `defineConfig(({ command, mode }) => …)` and reads values with `loadEnv(mode, ".", "")`. The `DEV_*` options are read only when `command === "serve"`. A `vite build` ignores them completely, even if they are set. Invalid values (an unknown scenario, a target that is not an `http(s)://` URL, or both variables set) throw a clear error when the dev server starts. This also picks up variables set in the shell, as in the commands above. It avoids using `process` directly, because `tsconfig.json` limits `types` to `vite/client`. Whether `tsc --noEmit` accepts this file unchanged is checked when it is implemented (see section 14).
- The fake plugin imports `buildMockResponse` from `src/api/mockStatus.ts`. It does not change it.
- **GitHub Codespaces.** Vite's dev server answers only to `localhost` and IP addresses unless told otherwise, and a codespace's forwarded URL is `https://<name>-5173.app.github.dev`. Whether Vite accepts that host could not be confirmed from the documentation, so the config sets `server.allowedHosts` to `.app.github.dev` (the value of `GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN`) **only** when the `CODESPACES=true` variable that GitHub sets in every codespace is present. Outside a codespace nothing changes. If this turns out to be unnecessary, the setting is harmless. It has not been tested in a codespace.
- Not covered: simulating a `429` or `Retry-After`, and failure injection against a real API. Those are the follow-up for the failure-injection spec. The rules are covered by unit tests instead.
- The variables are listed in the README with the commands above. A `.env.example` is not used because `.gitignore` excludes `.env.*`.

## 12. Steering updates

- `api-format.md`, Errors section, one sentence added: *"The frontend must not depend on the body of a non-2xx response, because API Gateway and CloudFront can return their own bodies (for example for 429 or 403)."* This is the gap: the file says every non-2xx response uses the `error` body, but `state-store-read-api/design.md` 5.2 already records that 429 comes from API Gateway before the Lambda runs. No other lines change. The edit happens in the implementation step, after the tasks are approved.
- `structure.md`: list `hooks/`, `format/`, the new `api/` files, `domain/worstStatus.ts`, and the `components/` additions. The rule "components render props only" stays true.

## 13. Test plan (Requirement 11)

All tests run offline under `npm test`. `vite.config.ts` `test.include` becomes `["src/**/*.test.{ts,tsx}"]`. The environment stays `node`.

| File | Covers |
|---|---|
| `api/config.test.ts` | Source default and values, interval default, clamp, numeric strings, junk values |
| `api/validateStatus.test.ts` | Valid bodies (`unknown` with nulls, `.000Z`), all rejection cases in 11.2, extra fields dropped, no throw on `null`/arrays/strings, error shows a field path and not the value |
| `api/pollSchedule.test.ts` | `parseRetryAfter` seconds, HTTP date with a fixed `nowMs`, past date, junk; `nextDelay` table in section 8 including the 20/40/60 s sequence and the floor/cap |
| `api/statusState.test.ts` | Transitions in section 9.1: 1 failure stays live, 2nd triggers `no-signal`, stale time unchanged, success resets, no-data cases, `aborted` unchanged |
| `api/fetchStatus.test.ts` | With a fake `fetch`: success, network error, 429 with and without `Retry-After`, 500, bad JSON, bad shape, abort. A non-2xx body is never read. |
| `api/pollController.test.ts` | `vi.useFakeTimers()` with a fake `fetchStatus` and a fake visibility object (an `EventTarget`): first request on `start()`, next after 10 s, no overlap, double `start()`, repeated and early `stop()`, restart after `stop()`, abort on stop, nothing published after `stop()`, hide/show pause and immediate resume, 429 backoff timing end to end, recovery after errors |
| `domain/worstStatus.test.ts` | Each position in the order, one service, duplicates, mixed lists |
| `format/staleSince.test.ts` | `Asia/Seoul` (`14:05 GMT+9`), a zone with a named abbreviation, midnight as `00:05`, minute padding |
| `components/Dashboard.test.tsx` | `renderToStaticMarkup`: loading; live; one failure (no overlay, not dimmed); no-signal with data (caption, `stale since 14:05 GMT+9`, dimmed class, data still present); no-signal without data (no pet, no "stale since"); worst status picks the pet while each service shows its own status; `—` for nulls; an event from an unknown `serviceId` shows the raw id; a known id shows the name |

Existing tests (`petState.test.ts`, `mockStatus.test.ts`) are not edited.
Server rendering cannot run effects, so React behavior is checked through the controller tests and the pure state functions. `useStatusPolling` itself is a short wrapper (section 9.3) and is covered by those.

## 14. Risks and known gaps

- **Tests may not run in this sandbox.** `node_modules` is not installed here (Node 22 is). During implementation I will try `npm ci`. If that fails or the tests cannot run, the report will say so, I will not claim the tests passed, and the pull request CI result is the evidence.
- **Typecheck of `vite.config.ts`.** Using `loadEnv` and a plugin with Node's `http` types depends on what `vite/client` and Vite's own typings expose. If `tsc --noEmit` rejects it, the fix stays inside `vite.config.ts` (a small local type), not a new dependency.
- **Newer toolchain.** `package.json` pins Vite 8 and Vitest 5. I will use only long-standing Vitest APIs (`vi.useFakeTimers`, `vi.fn`, `describe`/`it`/`expect`).
- **`Intl` zone names depend on the runtime's ICU data.** Node 22 has full ICU, so `Asia/Seoul` gives `GMT+9`. Users in other locales see their own short names. That is the intended result of using `Intl`.
- **No real-API test.** Nothing is deployed in this spec, so the real endpoint is never called. The validator is built from `api-format.md` and the backend's own tests, not from live responses.
- **CORS and CloudFront.** The relative path assumes the frontend and `/status` share an origin (design 6.3, option B). Hosting is a later spec.
- **Vite config-loader warning.** `vite.config.ts` imports `src/api/mockStatus` so that the dev-only fake can reuse the mock builder. Vite 8 then prints a warning on every `npm test`, `npm run dev` and `npm run build`: the config uses imports without a file extension, which the `configLoader: 'native'` mode that is planned to become the default in a future Vite major cannot resolve. It is a warning, not an error, and it changes nothing today. Fixing it needs file extensions in `mockStatus.ts` (which must not change here) or loading the module through `server.ssrLoadModule` (also planned for deprecation, and untested here). When Vite makes `native` the default, this import has to be revisited. Setting `VITE_CONFIG_NATIVE_IGNORE_WARNING=true` would hide the message, but it needs a package.json script or a CI change, which this spec does not make.
- **The dev tooling is not exercised by CI.** Vitest loads `vite.config.ts` on every run, so the file must parse and import correctly, and `tsc --noEmit` in the build checks its types. The middleware, the proxy and the Codespaces host setting only run in a dev server, which CI never starts. They are checked by hand (README, and the manual checklist in the stage 4 report).
- **Slight change to `mock` mode.** Event rows gain a service label (section 10).

## 15. Requirement coverage

| Requirement | Where |
|---|---|
| 1 Data source | 4, 10 |
| 2 Polling, controller | 7, 8, 9.2, 9.3 |
| 3 Validation | 6 |
| 4 Error state | 5, 7, 9.1, 10 |
| 5 Backoff | 8 |
| 6 Several services | 10, 10.1 |
| 7 Events | 10 |
| 8 Missing values | 10 |
| 9 Unchanged code, steering | 3, 11, 12 |
| 10 Dependencies, local dev | 2, 4, 11 |
| 11 Tests | 13, 14 |
