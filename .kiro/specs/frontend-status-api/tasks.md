# Tasks: Frontend connection to GET /status

Each task ends with `npm test` and `npm run typecheck` passing, if they can run in the sandbox (task 1 finds out). If they cannot run, the task is still done, but the report says plainly that nothing was executed, and the pull request CI result is the evidence.
`backend/`, `.github/workflows/ci.yml`, `src/domain/petState.ts`, `src/domain/types.ts` and `src/api/mockStatus.ts` are never edited. Nothing is deployed.
Requirement numbers refer to `requirements.md` and section numbers to `design.md`. Tests run offline.

## A. Setup

- [x] 1. Branch and baseline
  - The session starts on `main`, so create `feature/frontend-status-api` from it.
  - Try `npm ci`, then `npm test`, `npm run typecheck` and `npm run build` to record the baseline. If `npm ci` or the tests cannot run (no network, missing toolchain), write down exactly what failed and carry on without claiming anything passed.
  - Requirement 11.10. Design 14.

- [x] 2. Shared types and the test include pattern
  - Extend `src/api/types.ts` with `FailureReason`, `FetchOutcome`, `PollState` and `StatusView`. Do not rename or remove existing exports.
  - In `vite.config.ts` change only `test.include` to `["src/**/*.test.{ts,tsx}"]`.
  - Requirements 9.4, 9.5. Design 5, 13.

## B. Pure functions and their tests

- [x] 3. Configuration
  - Create `src/api/config.ts`: the constants (`DEFAULT_POLL_MS`, `MIN_POLL_MS`, `MAX_POLL_MS`, `MAX_BACKOFF_MS`), `resolveDataSource`, `resolvePollInterval` and `readConfig`.
  - Create `src/api/config.test.ts`: the source defaults to `mock` for unset, empty and other values, and only the exact string `api` selects `api`; the interval defaults to 10 000; values below 5 000 and above 10 000 are clamped; a numeric string works; `abc`, `""`, `NaN` and `Infinity` fall back to the default.
  - Requirements 1.1–1.2, 2.3, 11.3. Design 4.

- [x] 4. Response validation
  - Create `src/api/validateStatus.ts` as described in design 6. It returns the data or the failing field path, never throws, and builds a new object from the allowed fields only.
  - Create `src/api/validateStatus.test.ts`:
    - valid bodies: `unknown` with `null` `responseMs` and `lastChecked`, a timestamp with `Z`, and one with `.000Z`,
    - rejected: `"Healthy"`, a sixth status, a missing field, wrong types, a timestamp without `Z`, an offset such as `+09:00`, an impossible date such as `2026-02-30T00:00:00Z`, negative and fractional `responseMs`, 21 events, an empty `services` list, duplicate service ids, and `null`, an array and a string as the body,
    - extra fields are dropped from the output,
    - the error names the field path and does not contain the offending value.
  - Requirement 3, 11.2. Design 6.

- [x] 5. Delay and `Retry-After`
  - Create `src/api/pollSchedule.ts` with `parseRetryAfter(raw, nowMs)` and `nextDelay({ outcome, baseMs, currentMs, nowMs })`. No clock reads and no timers.
  - Create `src/api/pollSchedule.test.ts`: `Retry-After` in seconds and as an HTTP date with a fixed `nowMs`; a date in the past gives 0; `"-5"`, `"1.5"`, `"abc"`, `""` and `null` give `null`. For `nextDelay`: success gives the base; 429 with a valid header is clamped between the base and 60 s; 429 without a header doubles (20 s, 40 s, 60 s, 60 s with the default base); a header value larger than 60 s is capped; a header value smaller than the base is raised to the base; 500, network, invalid JSON and invalid shape give the base; a non-429 failure between two 429s resets to the base; `aborted` keeps the current delay.
  - Requirement 5, 11.3. Design 8.

- [x] 6. State transitions
  - Create `src/api/statusState.ts` with `FAILURES_BEFORE_NO_SIGNAL`, `initialPollState`, `applyOutcome` and `toView`.
  - Create `src/api/statusState.test.ts`: success sets data and time and clears failures; 1 failure after data stays `live`; the 2nd consecutive failure gives `no-signal` with the data and the original `receivedAt`; more failures do not move `staleSinceMs`; a success after `no-signal` returns to `live` and resets the count; with no data, 1 failure stays `loading` and 2 give `no-signal` with `data` and `staleSinceMs` both `null`; `aborted` returns the same state and does not count as a failure; `lastFailure` carries the reason and is cleared by a success.
  - Requirement 4, 11.4. Design 9.1.

- [x] 7. Worst status
  - Create `src/domain/worstStatus.ts` with `SEVERITY_ORDER` and `worstStatus`. It imports only `domain/types`.
  - Create `src/domain/worstStatus.test.ts`: each position in the order (down, slow, recovering, unknown, healthy) wins over everything after it; one service returns its own status; duplicates; mixed lists; one `unknown` among `healthy` services gives `unknown`; an empty list gives the `unknown` fallback.
  - Requirement 6, 11.6. Design 10.1.

- [x] 8. "stale since" formatter
  - Create `src/format/staleSince.ts` with `formatStaleSince(ms, opts?)` as described in design 10.2.
  - Create `src/format/staleSince.test.ts` with fixed instants, `locale: "en-US"` and explicit zones: `Asia/Seoul` gives `14:05 GMT+9` for the matching instant; `America/New_York` gives a named abbreviation (`EST` or `EDT`, depending on the date); a time at local midnight gives `00:05`, not `24:05`; minutes and hours are zero-padded.
  - Requirement 4.5, 11.7. Design 10.2.

## C. Data layer and controller

- [x] 9. Fetch wrapper
  - Create `src/api/fetchStatus.ts` as described in design 7. It takes `fetchImpl` as a parameter.
  - Create `src/api/fetchStatus.test.ts` with a fake `fetch` (no network): success; a thrown network error; 429 with and without `Retry-After` (the header value is returned raw); 500; a body that is not JSON; a body that fails validation; an aborted signal gives `aborted`, not a failure; the body of a non-2xx response is never read (a fake body that throws when read).
  - Requirements 4.1, 4.2, 4.10, 11.1. Design 7.

- [x] 10. Polling controller
  - Create `src/api/pollController.ts` as described in design 9.2. It does not import React.
  - Create `src/api/pollController.test.ts` with `vi.useFakeTimers()`, a fake `fetchStatus`, and a fake visibility object built on `EventTarget`:
    - `start()` sends the first request at once and the next one after the interval; requests never overlap,
    - `start()` twice leaves one loop, one request and one listener,
    - `stop()` twice, `stop()` before `start()`, and `stop()` after `stop()` do not throw,
    - `start()` after `stop()` runs exactly one loop,
    - `stop()` aborts the in-flight request, clears the timer, removes the listener, and a response that arrives afterwards publishes nothing and schedules nothing,
    - hiding the tab clears the timer and aborts the in-flight request without counting a failure, and showing it again requests at once; a `visible` event during an in-flight request does not start a second one,
    - the 429 sequence end to end (20 s, 40 s, 60 s), `Retry-After` honored, a success returning to 10 s,
    - 1 failure then success never reaches `no-signal`; 2 failures then success recovers without user action.
  - Requirements 2, 4.9, 4.10, 5, 11.1, 11.3–11.5. Design 9.2.

## D. Components, hook and wiring

- [x] 11. Service list and event labels
  - Create `src/components/ServiceList.tsx`: one row per service in API order, with name, status as text plus the existing `status--{status}` class, Response and Checked, and "—" for `null` values.
  - Update `src/components/EventLog.tsx`: optional `services` prop, the service label (name, or the raw `serviceId` when unknown or when the prop is missing), and a unique `key`. Text only, no HTML insertion.
  - Requirements 6.4–6.5, 7, 8. Design 10.

- [x] 12. Overlay, Dashboard and styles
  - Create `src/components/NoSignal.tsx` (`role="status"`, "NO SIGNAL", "Can't reach the ghost. Retrying…", and "stale since …" only when a time is given) and `src/components/Dashboard.tsx` as described in design 10.
  - Add `.frame`, `.frame--stale`, `.no-signal` and `.services` styles to `src/styles.css`, with dimming by opacity plus text, not color alone. Read the file first and reuse its existing look.
  - Create `src/components/Dashboard.test.tsx` with `renderToStaticMarkup`, a fixed `staleSinceMs` and fixed time zone:
    - loading state,
    - live state,
    - one failure: no overlay and no dimming class,
    - no-signal with data: caption, `stale since 14:05 GMT+9`, dimmed class, and the pet, rows and events still in the markup,
    - no-signal without data: caption, no "stale since", no pet,
    - the pet follows the worst status while each row keeps its own status text,
    - `—` for `null` values,
    - an event from an unknown `serviceId` shows the raw id, a known id shows the name,
    - the demo children render only when passed.
    - `formatStaleSince` must be given the fixed zone, so `NoSignal` and `Dashboard` take an optional `timeZone` prop for tests (default: the viewer's zone).
  - Requirements 4, 6, 7, 8, 11.8. Design 10.

- [x] 13. Hook and `App` wiring
  - Create `src/hooks/useStatusPolling.ts` as described in design 9.3.
  - Rewrite `src/App.tsx`: `readConfig(import.meta.env)`, `ApiApp` and `MockApp`, with the demo buttons only in `MockApp`. `buildMockResponse` and the demo buttons keep working as before.
  - Check: `npm run typecheck` and `npm run build` with no variables (default `mock`), and `VITE_DATA_SOURCE=api npm run build`. The existing `petState` and `mockStatus` tests still pass unchanged.
  - Requirements 1, 2.7, 2.8, 9. Design 9.3, 10.

## E. Dev tooling, docs and steering

- [ ] 14. Dev-only fake `/status` and proxy
  - In `vite.config.ts` add `DEV_FAKE_STATUS` (plugin with `apply: "serve"`, `configureServer` only, serving `buildMockResponse(scenario)` with `Cache-Control: no-store`) and `DEV_API_TARGET` (`server.proxy`), read through `loadEnv`. Setting both, or an unknown scenario, throws a clear error at config load. The plugin name and its messages contain `fake-status`.
  - If `tsc --noEmit` rejects the file, fix it inside `vite.config.ts` with a small local type. Do not add a dependency.
  - Requirements 9.5, 10.1–10.5. Design 11.

- [ ] 15. Verify the dev tooling stays out of production
  - Run the four checks in design 11.1:
    - `grep -rn "DEV_" src/` finds nothing,
    - build three times: no variables, `VITE_DATA_SOURCE=api`, and `DEV_FAKE_STATUS=healthy`,
    - `grep -rE "DEV_FAKE_STATUS|DEV_API_TARGET|fake-status" dist/` finds nothing after each build,
    - `grep -rE "execute-api|amazonaws|localhost" dist/` finds nothing.
  - If the dev server can start in the sandbox, start it in the background with `DEV_FAKE_STATUS=healthy`, request `/status` with `curl` and check the JSON, and check that `DEV_API_TARGET` plus `DEV_FAKE_STATUS` together fails. Stop the server afterwards. If it cannot, say so.
  - Record the commands and results for the PR description.
  - Requirements 10.1, 10.5. Design 11.1.

- [ ] 16. README
  - Update `README.md` with the build-time variables (`VITE_DATA_SOURCE`, `VITE_POLL_INTERVAL_MS`, the defaults and the 5–10 s range), the local `api` mode commands from design 11, the note that the checker does not exist yet so a real API shows every service as `unknown`, and a pointer to the failure-injection follow-up. No `.env.example`.
  - Requirements 10.3–10.5. Design 11, 10.1.

- [ ] 17. Steering
  - Add one sentence to the Errors section of `.kiro/steering/api-format.md`: the frontend must not depend on the body of a non-2xx response, because API Gateway and CloudFront can return their own bodies (for example for 429 or 403). No other line changes.
  - Update `.kiro/steering/structure.md` to list `hooks/`, `format/`, the new `api/` files, `domain/worstStatus.ts` and the new components.
  - Requirements 9.6, 9.7. Design 12.

## F. Review

- [ ] 18. Final checks and pull request
  - Run `npm test`, `npm run typecheck` and `npm run build` if possible. Check, with `git diff main --stat`, that `backend/`, `.github/`, `src/domain/petState.ts`, `src/domain/types.ts`, `src/api/mockStatus.ts` and its tests are untouched, and that the only `vite.config.ts` changes are the include pattern and the dev tooling. Check that no secrets or `.env` files are committed and that `package.json` and the lock file have no new dependencies.
  - Re-read `requirements.md` and tick each acceptance criterion against the result. List any criterion that could not be verified.
  - Commit on `feature/frontend-status-api`, push, and open a PR into `main` with `gh api`. The PR description lists: what changed, the commands and results from task 15, which checks ran in the sandbox and which did not (no claim of passing tests that did not run), the visible change in `mock` mode (event service labels), the `unknown` waiting-pet behavior until the checker exists, and the known gaps from design 14.
  - Requirements 8, 9, 11.9–11.10. Design 14.

## Follow-ups (not in this spec)
- Failure-injection spec: a real failure-injection control for `api` mode (including 429 and `Retry-After`).
- Frontend hosting spec: S3 and CloudFront with the `/status` behavior from `state-store-read-api` design 6.3.
- Checker spec: until the checker runs, the real API shows every service as `unknown`.
