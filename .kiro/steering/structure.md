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
  api/         Data access. Today: mock data. Later: fetch to API Gateway.
    types.ts       StatusResponse (the API contract, see api-format.md)
    mockStatus.ts  Demo scenarios built by running the real domain logic
    mockStatus.test.ts
  components/  Presentational React components. Render props only, no data fetching.
  App.tsx      Wires the data source to components.
  main.tsx     Entry point.
backend/       SAM template and Python 3.12 Lambdas
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
.kiro/steering/  Team rules (this folder)
.kiro/specs/     Feature specs
```

## Rules
- Dependencies point inward: `components -> api types`, `api -> domain`. `domain` imports nothing outside `domain`.
- The UI reads only `StatusResponse`. Swapping mock data for the real API must not change any component.
- The Python checker must implement the same rules as `src/domain/petState.ts` and reuse the same test scenarios. If one changes, change both.
- Thresholds live as named constants at the top of `petState.ts`. Do not inline magic numbers elsewhere.
- Backend: keys, status values, timestamp format and item validation live only in `ghostpet/model.py`. The reader and writer import them and never redefine them.
- Backend: handlers do I/O only. Logic that can be pure (shaping, validation, decisions) lives in `ghostpet/` and is unit-tested without AWS.
- Backend: run tests with `cd backend && python -m pytest`.
