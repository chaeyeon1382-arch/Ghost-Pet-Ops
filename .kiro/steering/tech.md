---
inclusion: always
---
# Tech Stack

Ghost Pet Ops is a retro virtual pet whose mood reflects the health of a monitored web service.
Built by 2 developers in about 40 person-hours for Kiroween 2026 (Frankenstein category).

## Frontend
- React 18 + TypeScript (strict mode), bundled with Vite.
- Plain CSS in `src/styles.css`. No UI framework, no CSS-in-JS.
- No state-management library. React state is enough at MVP scale.
- Tests: Vitest (`npm test`, which runs `vitest --run`). Never use watch mode in automation.

## Backend (planned, not built yet)
- AWS Lambda, Python 3.12, one function per job: `checker` (scheduled health checks) and `status` (read API).
- EventBridge Scheduler triggers `checker` (default every 1 minute).
- DynamoDB (on-demand billing) stores current service state and the state-change events.
- API Gateway HTTP API exposes `GET /status`.
- AWS SAM (`template.yaml`) defines all infrastructure. No click-ops.

## Dependency policy
- Keep dependencies minimal. Adding a runtime dependency requires a short justification in the PR.
- Prefer the standard library (Python) and the platform (browser APIs) over packages.

## Out of scope for now
- Authentication, AI features, multi-tenant support.
