# Design: Ghost Pet Ops MVP

## Overview
Three layers with one-way dependencies:

```
components (React)  ->  api (StatusResponse)  ->  domain (pure logic)
```

- `domain/` decides state from check results. It is pure TypeScript, unit-tested, and will be ported to the Python `checker` Lambda.
- `api/` produces a `StatusResponse`. Today it builds mock data by running scripted checks through `domain`. Later it calls `fetch("/status")`.
- `components/` render a `StatusResponse` and nothing else.

## Domain model
```ts
type PetState = "healthy" | "slow" | "down" | "recovering";
interface CheckResult { statusCode: number | null; responseMs: number; timedOut: boolean }
interface Tracker { state: PetState; slowStreak: number; fastStreak: number; failStreak: number }
decide(prev: Tracker, check: CheckResult): { tracker: Tracker; transition?: { from; to; note } }
```

The `Tracker` holds the state and three streak counters. In the backend, the Tracker is the item stored per service in DynamoDB, so each Lambda run needs only the previous item and the new check.

### Counter updates (applied before the state rule)
| Check | failStreak | slowStreak | fastStreak |
|---|---|---|---|
| failure | +1 | 0 | 0 |
| success > 2000 ms | 0 | +1 | 0 |
| success <= 2000 ms | 0 | 0 | +1 |

### State rules
| Current | Failure | Success |
|---|---|---|
| healthy | down if failStreak >= 3, else stay | slow if slowStreak >= 2, else stay |
| slow | down if failStreak >= 3, else stay | healthy if fastStreak >= 2, else stay |
| down | stay (no new event) | recovering |
| recovering | down (immediately) | healthy |

A transition is emitted only when the state changes. The thresholds are constants in `petState.ts`:
`TIMEOUT_MS = 5000`, `SLOW_THRESHOLD_MS = 2000`, `SLOW_CHECKS_NEEDED = 2`, `FAILURES_TO_DOWN = 3`.

### Decisions and open questions
- **Exactly 2000 ms counts as fast.** This follows "at or under 2000 ms" in the slow -> healthy rule.
- **Recovering ignores response time.** A slow success while recovering still goes to healthy. If it should go to slow instead, one branch changes. *Open question.*
- **3xx counts as success.** The checker should not follow redirects and should use the redirect response as the result. *Open question.*
- **Streaks carry across transitions.** For example, after healthy -> slow the slow streak keeps counting. This has no visible effect under the current rules.

## Mock data and demo
`DEMO_SCENARIOS` maps each state to a check sequence:
- healthy: `[200 84ms]`
- slow: `[200 2300ms, 200 2450ms]`
- down: `[timeout, 503, timeout]`
- recovering: `[timeout, 503, timeout, 200 140ms]`

`buildMockResponse()` runs the sequence through `runChecks()`, assigns timestamps one minute apart, and returns a `StatusResponse`.

## UI
- ASCII ghost in a `<pre>`, one drawing per state, with per-state color and animation (float, slow float, faded, blink).
- Stats list (`aria-live="polite"`), event log, and demo buttons.
- Green-on-black monospace theme. Respects `prefers-reduced-motion`.

## Future backend mapping (not built in this phase)
- EventBridge Scheduler (rate 1 minute) -> `checker` Lambda: HTTP GET with a 5 s timeout, load the Tracker from DynamoDB, run `decide`, save the Tracker, and write an event item if the state changed.
- API Gateway `GET /status` -> `status` Lambda: read services and recent events and return a `StatusResponse`.
- DynamoDB single table: `PK=SERVICE#<id>`, `SK=STATE` for the Tracker, `SK=EVENT#<iso>` for events. Optional TTL on events.

## Test plan
| # | Scenario | Expected |
|---|---|---|
| 1 | 200, 150 ms | healthy |
| 2 | 200, 2300 ms once | stays healthy |
| 3 | 200, 2300 ms twice in a row | slow |
| 4 | one timeout | unchanged |
| 5 | three timeouts or 5xx in a row (also mixed) | down |
| 6 | down, then one success | recovering |
| 7 | recovering, then one more success | healthy |

Extra edge cases: streak resets, slow -> healthy, slow -> down, failure while recovering, the 2000 ms boundary, and input immutability.
