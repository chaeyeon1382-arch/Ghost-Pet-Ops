---
inclusion: always
---
# Project Structure

```
src/
  domain/      Pure TypeScript. State decision logic and types. No React, no I/O, no clock.
    types.ts       Status, CheckResult, Tracker (status + counters), Transition
    petState.ts    decide(), runChecks(), classify(), isFailure(), thresholds
    petState.test.ts
    worstStatus.ts SEVERITY_ORDER, worstStatus(): which status the pet shows for several services (display only)
  api/         Data access. Mock data or GET /status. No React.
    types.ts       StatusResponse (the API contract, see api-format.md), plus FetchOutcome, PollState, StatusView
    mockStatus.ts  Demo scenarios built by running the real domain logic
    config.ts      Build-time settings: data source (VITE_DATA_SOURCE) and poll interval (VITE_POLL_INTERVAL_MS)
    validateStatus.ts  Checks a parsed /status body against api-format.md (pure)
    fetchStatus.ts One GET /status request. Returns a FetchOutcome, never rejects
    pollSchedule.ts   Retry-After parsing and the next delay, including 429 backoff (pure)
    statusState.ts    applyOutcome() and toView(): good data, failures, "no signal" after 2 in a row (pure)
    pollController.ts The polling loop: timers, abort, hidden-tab pause. start() and stop() are idempotent
    *.test.ts      Tests next to each file. They run offline with fake fetch and fake timers.
  hooks/       React glue only.
    useStatusPolling.ts  Starts a PollController on mount and stops it on unmount
  format/      Pure formatting helpers.
    staleSince.ts  "HH:MM <zone>" for the "stale since" text
  components/  Presentational React components. Render props only, no data fetching.
    Dashboard.tsx  Takes a StatusView and shows the pet, service rows, events and the overlay
    ServiceList.tsx, NoSignal.tsx, EventLog.tsx, Pet.tsx
  App.tsx      Picks the data source (mock or api) and wires it to Dashboard.
  main.tsx     Entry point.
backend/       SAM template and Python 3.12 Lambdas
  README.md              Tests, deploy order, placeholder URLs, checker author notes
  template.yaml          All AWS resources. Every function uses CodeUri: src/
  requirements-dev.txt   Local test dependencies (pytest). Not packaged.
  src/                   Lambda code root, packaged into every function
    requirements.txt       Runtime dependencies. Empty: boto3 ships with the runtime.
    status_app.py          GET /status handler
    checker_app.py         (planned) scheduled health-check handler
    ghostpet/              Shared package, imported by both handlers
      model.py               Statuses, keys, timestamp format, TTL, idempotency token, item validation
      config.py              Loads and validates services.json
      services.json          Monitored services and the URL allowlist
      status_response.py     Reader-only response shaping (pure)
      decision.py            (planned) Python port of src/domain/petState.ts
  tests/                 pytest, outside src/ so never packaged
frontend/      CloudFormation: private S3 bucket + CloudFront, /status on the same domain
  template.yaml
demo/          SAM: controllable demo target (Lambda Function URL, MODE = ok | slow | down). Deleted after the demo.
  template.yaml
  src/app.py
ops/           CloudFormation: monthly cost budget with email alerts
  budget.yaml
docs/          Demo script, layout check list, screenshot plan
.kiro/steering/  Team rules (this folder)
.kiro/specs/     Feature specs
```

## Rules
- Dependencies point inward: `components -> api types, domain, format`, `hooks -> api`, `api -> domain`. `domain` imports nothing outside `domain`. `api/` and `domain/` never import React.
- The UI reads only `StatusView` (which wraps `StatusResponse`). Swapping mock data for the real API must not change any component.
- "No signal" is a UI state (`StatusView`), never a sixth `Status`.
- `DEV_*` variables are read only by `vite.config.ts`. Nothing under `src/` reads them, and production builds ignore them.
- The Python checker must implement the same rules as `src/domain/petState.ts` and reuse the same test scenarios. If one changes, change both.
- Thresholds live as named constants at the top of `petState.ts`. Do not inline magic numbers elsewhere.
- Backend: keys, status values, timestamp format and item validation live only in `ghostpet/model.py`. The reader and writer import them and never redefine them.
- Backend: handlers do I/O only. Logic that can be pure (shaping, validation, decisions) lives in `ghostpet/` and is unit-tested without AWS.
- Backend: run tests with `cd backend && python -m pytest`.
