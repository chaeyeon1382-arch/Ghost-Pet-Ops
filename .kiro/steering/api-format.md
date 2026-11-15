---
inclusion: always
---
# API Response Format

`GET /status` returns JSON. The mock data in `src/api/mockStatus.ts` uses the same shape.
TypeScript source of truth: `src/api/types.ts`.

```json
{
  "services": [
    { "id": "site-1", "name": "Example", "status": "healthy",
      "responseMs": 84, "lastChecked": "2026-11-15T02:00:00Z" }
  ],
  "events": [
    { "serviceId": "site-1", "from": "healthy", "to": "slow",
      "at": "2026-11-15T01:58:00Z", "note": "response over 2s twice in a row" }
  ]
}
```

## Field rules
- `status`, `from`, `to`: one of `healthy`, `slow`, `down`, `recovering`.
- `responseMs`: integer milliseconds of the most recent check. On timeout, the timeout value (5000).
- `lastChecked`, `at`: ISO 8601 in UTC with a `Z` suffix.
- `events`: newest first. Only real state changes, never one entry per check.
- `note`: short human-readable reason. It must not contain URLs with credentials, tokens, or response bodies.
- Adding fields is allowed. Renaming or removing fields requires updating `src/api/types.ts`, this file, and the Lambda together.

## Errors
- Non-2xx responses return `{ "error": { "code": "string", "message": "string" } }`.
- Never return stack traces or internal AWS identifiers.
