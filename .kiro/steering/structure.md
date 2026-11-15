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
backend/       (planned) SAM template and Python Lambdas
  template.yaml
  functions/checker/   health check + state decision (Python port of src/domain)
  functions/status/    GET /status
.kiro/steering/  Team rules (this folder)
.kiro/specs/     Feature specs
```

## Rules
- Dependencies point inward: `components -> api types`, `api -> domain`. `domain` imports nothing outside `domain`.
- The UI reads only `StatusResponse`. Swapping mock data for the real API must not change any component.
- The Python checker must implement the same rules as `src/domain/petState.ts` and reuse the same test scenarios. If one changes, change both.
- Thresholds live as named constants at the top of `petState.ts`. Do not inline magic numbers elsewhere.
