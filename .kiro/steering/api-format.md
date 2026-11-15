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
- `status`, `from`, `to`: lowercase, one of `unknown`, `healthy`, `slow`, `down`, `recovering`. `unknown` means no status has been decided yet.
- `responseMs`: integer milliseconds of the most recent check, or `null` if no check has run. On timeout, the timeout value (5000).
- `lastChecked`: ISO 8601 in UTC with a `Z` suffix, or `null` if no check has run.
- `at`: ISO 8601 in UTC with a `Z` suffix.
- `events`: the 20 most recent, newest first. Only real status transitions (including `unknown` -> first status), never one entry per check.
- The checker's counters (`consecutiveFailures`, `consecutiveSlow`, `consecutiveFast`) are stored in DynamoDB and never appear in this response.

Example before the first check:

```json
{
  "services": [
    { "id": "site-1", "name": "Example", "status": "unknown",
      "responseMs": null, "lastChecked": null }
  ],
  "events": []
}
```
- `note`: short human-readable reason. It must not contain URLs with credentials, tokens, or response bodies.
- Adding fields is allowed. Renaming or removing fields requires updating `src/api/types.ts`, this file, and the Lambda together.

## Errors
- Non-2xx responses return `{ "error": { "code": "string", "message": "string" } }`.
- Never return stack traces or internal AWS identifiers.
