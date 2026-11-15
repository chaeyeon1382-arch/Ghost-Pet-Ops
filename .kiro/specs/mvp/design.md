# Design: Ghost Pet Ops MVP

## Overview
Three layers with one-way dependencies:

```
components (React)  ->  api (StatusResponse)  ->  domain (pure logic)
```

- `domain/` decides the status from check results. It is pure TypeScript, unit-tested, and will be ported to the Python `checker` Lambda.
- `api/` produces a `StatusResponse`. Today it builds mock data by running scripted checks through `domain`. Later it calls `fetch("/status")`.
- `components/` render a `StatusResponse` and nothing else.

## Domain model
```ts
type Status = "unknown" | "healthy" | "slow" | "down" | "recovering";
interface CheckResult { statusCode: number | null; responseMs: number; timedOut: boolean }
interface Tracker {
  status: Status;
  consecutiveFailures: number;
  consecutiveSlow: number;
  consecutiveFast: number;
}
decide(prev: Tracker, check: CheckResult): { tracker: Tracker; transition?: { from; to; note } }
```

`initialTracker` is `{ status: "unknown", consecutiveFailures: 0, consecutiveSlow: 0, consecutiveFast: 0 }`.
In the backend, the Tracker is the item stored per service in DynamoDB. Each checker run needs only the previous item and the new check. If no item exists yet, the checker starts from `initialTracker`.

### Check classification
| Check | Class |
|---|---|
| `timedOut` is true (5 s) | failure |
| `statusCode` is null (connection error) | failure |
| `statusCode` 400 to 599 | failure |
| anything else (including 1xx, 2xx, 3xx), `responseMs <= 2000` | fast success |
| anything else, `responseMs > 2000` | slow success |

### Counter updates (applied before the status rules)
| Check | consecutiveFailures | consecutiveSlow | consecutiveFast |
|---|---|---|---|
| failure | +1 | 0 | 0 |
| fast success | 0 | 0 | +1 |
| slow success | 0 | +1 | 0 |

### Status rules (after counters are updated)
| Current | Failure | Fast success | Slow success |
|---|---|---|---|
| unknown | down if consecutiveFailures >= 3, else stay | healthy | slow if consecutiveSlow >= 2, else stay |
| healthy | down if consecutiveFailures >= 3, else stay | stay | slow if consecutiveSlow >= 2, else stay |
| slow | down if consecutiveFailures >= 3, else stay | healthy if consecutiveFast >= 2, else stay | stay |
| down | stay (no event) | recovering | recovering |
| recovering | down (immediately) | healthy if consecutiveFast >= 2, else stay | slow if consecutiveSlow >= 2, else stay |

Consequences worth noting:
- down, fast, fast -> recovering, then healthy (the first fast success counts).
- down, slow, slow -> recovering, then slow (the first slow success counts).
- down, slow, fast, fast -> recovering, recovering, healthy.
- down, fast, slow, fast -> recovering throughout, because each switch between fast and slow resets the other counter.
- Counters are not reset on a transition. Only the counter rules above change them.

A transition is emitted only when the status changes. Thresholds are constants in `petState.ts`:
`TIMEOUT_MS = 5000`, `SLOW_THRESHOLD_MS = 2000`, `SLOW_CHECKS_NEEDED = 2`, `FAST_CHECKS_NEEDED = 2`, `FAILURES_TO_DOWN = 3`.

### Resolved decisions (task 7)
- **Exactly 2000 ms is fast. 2001 ms is slow.**
- **3xx counts as success.** The checker does not follow redirects and uses the redirect response as the result.
- **Recovering requires two consecutive fast successes to become healthy.** A single slow success keeps the status at recovering and resets `consecutiveFast`.
- **Recovering becomes slow after two consecutive slow successes**, so a service that stays slow after an outage does not stay recovering forever.
- **Response fields before the first check are `null`.** `responseMs` and `lastChecked` are nullable in `src/api/types.ts` and `api-format.md`. The UI shows "—".

## Events
- An event is created only when `decide` returns a transition, including `unknown -> <first status>`.
- `GET /status` returns at most the 20 most recent events, newest first (`MAX_EVENTS = 20`).
- The mock builder applies the same limit.

## API response
The format is unchanged except for the `unknown` status value and nullable `responseMs` and `lastChecked`. Counters are stored with the Tracker and are never included in the `/status` response.

## Mock data and demo
`DEMO_SCENARIOS` maps each status to a check sequence that ends in that status:
- unknown: `[]` (no checks yet)
- healthy: `[200 84ms]` (unknown -> healthy)
- slow: `[200 2300ms, 200 2450ms]` (unknown -> slow)
- down: `[200 90ms, timeout, 503, timeout]` (unknown -> healthy -> down)
- recovering: `[200 90ms, timeout, 503, timeout, 200 2400ms]` (... -> down -> recovering; the slow success keeps it recovering)

`buildMockResponse()` runs the sequence through `runChecks()`, assigns timestamps one minute apart, keeps the 20 most recent events, and returns a `StatusResponse`.

## UI
- ASCII ghost in a `<pre>`, one drawing per status, with per-status color and animation:
  healthy (float), slow (slow float), down (faded), recovering (blink), unknown (neutral grey, static, caption "Waiting for the first health check...").
- Stats list (`aria-live="polite"`) shows "—" for response time and last check while they are `null`.
- Event log and five demo buttons.
- Green-on-black monospace theme. Respects `prefers-reduced-motion`.

## Future backend mapping (not built in this phase)
- EventBridge Scheduler (rate 1 minute) -> `checker` Lambda: HTTP GET with a 5 s timeout and no redirect following, load the Tracker from DynamoDB (or `initialTracker`), run `decide`, save the Tracker, and write an event item if the status changed.
- API Gateway `GET /status` -> `status` Lambda: read the services and the 20 most recent events, and return a `StatusResponse` without counters.
- DynamoDB single table: `PK=SERVICE#<id>`, `SK=STATE` for the Tracker, `SK=EVENT#<iso>` for events (query descending, `Limit=20`). Optional TTL on events.

## Test plan
"From X" means the test starts with a Tracker in status X and zero counters. `ok(ms)` is a 200 response.

| # | Scenario | Expected |
|---|---|---|
| 1 | from unknown: 200, 150 ms | healthy |
| 2 | from healthy: 200, 2300 ms once | stays healthy |
| 3 | from healthy: 200, 2300 ms twice in a row | slow |
| 4 | from healthy: one timeout | unchanged, no event |
| 5 | from healthy: three timeouts, three 5xx, or a mix in a row | down |
| 6 | from down: one success (fast or slow) | recovering |
| 7 | from down: fast, fast | recovering, then healthy |
| 8 | boundary: 2000 ms is fast, 2001 ms is slow | classification and counters |
| 9 | from recovering: one failure | down |
| 10 | failure, failure, success, failure, failure | consecutiveFailures resets; status unchanged |
| 11 | slow, fast, slow from healthy | consecutiveSlow resets; stays healthy |
| 12 | from unknown: one fast success | healthy, event unknown -> healthy |
| 13 | from unknown: two slow successes | slow (stays unknown after the first) |
| 14 | from unknown: three failures | down (stays unknown after one or two) |
| 15 | from down: fast, slow | stays recovering, consecutiveFast = 0 |
| 16 | 301 and 304, fast | success |
| 17 | 404, connection error (`statusCode` null) | failure |
| 18 | from down: slow, slow | recovering, then slow |
| 19 | from recovering: slow, slow | slow |
| 20 | from down: slow, fast, slow | stays recovering (each switch resets the other counter) |

Extra edge cases: slow -> healthy needs two fast successes, slow -> down, down stays down on failure with no event, input immutability, and the mock builder keeps at most 20 events.
