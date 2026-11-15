# Tasks: Ghost Pet Ops MVP

- [x] 1. Scaffold React + TypeScript with Vite and Vitest (minimal dependencies)
- [x] 2. Steering docs: tech, structure, api-format, security
- [x] 3. Domain module `src/domain/petState.ts` (pure decision logic)
  - Requirements 1, 2
- [x] 4. Unit tests for the 7 scenarios plus edge cases
  - Requirement 5
- [x] 5. API types and mock data built through the domain logic
  - Requirements 3.4, 4.2
- [x] 6. Pet UI, stats, event log, demo buttons
  - Requirements 3, 4
- [ ] 7. Resolve open questions in design.md (recovering + slow response, 3xx handling)
- [ ] 8. Next phase: SAM template, Python `checker` and `status` Lambdas, port the tests to pytest
- [ ] 9. Next phase: replace `buildMockResponse` with `fetch("/status")` and polling
