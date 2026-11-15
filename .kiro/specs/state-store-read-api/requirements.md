# Requirements: State Store and Read API

## Introduction
This spec covers the DynamoDB state store and the read-only `GET /status` API for Ghost Pet Ops.
A teammate will build the health-check Lambda (`checker`) later. This spec defines the stored item
shapes and write rules that the checker must follow, but it does not implement the checker.

In scope: the table, the service configuration list, the `status` Lambda, API Gateway, IAM roles for the
reader and the future writer, a SAM template, and pytest unit tests for response shaping.

Out of scope: deploying anything or creating AWS resources, the checker Lambda and its schedule,
changes to `src/domain/petState.ts`, changes to the CI workflow, authentication, and AI features.

The steering documents in `.kiro/steering/` apply, in particular `api-format.md` and `security.md`.

## Glossary
- **Status**: one of `unknown`, `healthy`, `slow`, `down`, `recovering` (lowercase), as in `api-format.md`.
- **Configured service**: an entry `{ id, name }` in the service configuration list. The checker's URL allowlist also lives in this list.
- **Service record**: the stored current state of one configured service.
- **Event**: a stored record of one status transition.
- **Counters**: `consecutiveFailures`, `consecutiveSlow`, `consecutiveFast`.
- **Checker**: the future health-check Lambda, the only writer to the table.
- **Reader**: the `status` Lambda behind `GET /status`.

## Requirement 1: Single table
**User story:** As a developer, I want one table for all state so that the system stays simple and cheap.

Acceptance criteria:
1. The system SHALL use exactly one DynamoDB table for both service records and events.
2. The table SHALL use on-demand billing.
3. The table SHALL be defined in the SAM template and SHALL NOT require manual setup.
4. All reads needed by `GET /status` SHALL use `GetItem` or `Query`. The system SHALL NOT use `Scan`.

## Requirement 2: Service record
**User story:** As the checker author, I want a clear record shape so that I know exactly what to read and write.

Acceptance criteria:
1. A service record SHALL hold `id`, `name`, `status`, `responseMs`, `lastChecked`, `consecutiveFailures`, `consecutiveSlow`, and `consecutiveFast`.
2. `status` SHALL be one of the five lowercase status values.
3. `responseMs` SHALL be a non-negative integer in milliseconds.
4. `lastChecked` SHALL be a UTC ISO 8601 string with a `Z` suffix.
5. Counters SHALL be non-negative integers.
6. A service record MAY hold additional internal attributes (keys, a version number) that the API never returns.

## Requirement 3: Safe concurrent writes
**User story:** As the checker author, I want the write rules defined up front so that two overlapping invocations cannot corrupt a record or lose an event.

The checker is not built in this spec. These criteria define the contract that design.md documents and that the checker must follow.

Acceptance criteria:
1. Every update to a service record SHALL be conditional on the record not having changed since it was read (optimistic locking).
2. The first write for a service SHALL succeed only if no record exists yet.
3. A write SHALL NOT replace a record with the result of an older check (`lastChecked` must move forward).
4. WHEN a status transition occurs THEN the record update and the event write SHALL succeed or fail together.
5. WHEN a conditional write fails THEN the checker SHALL NOT overwrite the record unconditionally. It SHALL re-read the record and either re-apply its check or drop it, as described in design.md.
6. Retrying the same write SHALL NOT create duplicate events.
7. design.md SHALL describe these rules step by step, with the exact DynamoDB condition expressions, so that the teammate can implement them without guessing.

## Requirement 4: Events
**User story:** As a viewer, I want recent status changes kept for a week so that the history is useful without growing forever.

Acceptance criteria:
1. An event SHALL hold `serviceId`, `from`, `to`, `at`, and `note`.
2. `from` and `to` SHALL be lowercase status values. `at` SHALL be a UTC ISO 8601 string with a `Z` suffix.
3. Each event SHALL carry a TTL attribute in epoch seconds, set to `at` plus the retention period.
4. The retention period SHALL default to 7 days and SHALL be adjustable through a SAM parameter without code changes.
5. The table SHALL have DynamoDB TTL enabled on that attribute.
6. Because DynamoDB deletes expired items with a delay, the reader SHALL NOT return events whose TTL has passed.

## Requirement 5: Service configuration
**User story:** As an operator, I want the monitored services defined in a reviewed file so that only pre-approved URLs are ever checked.

Acceptance criteria:
1. The configured services SHALL come from a small list kept in a repo file or a SAM parameter. Users SHALL NOT be able to add services at runtime.
2. Each entry SHALL have a unique `id` and a `name`. Entries used by the checker SHALL also have an `https://` URL.
3. The checker SHALL only check URLs from this list.
4. The reader SHALL return services in the order of the list.
5. WHEN the list is empty or invalid (duplicate ids, missing fields) THEN the reader SHALL return an error response instead of partial data.
6. The list SHALL NOT contain secrets.

## Requirement 6: GET /status
**User story:** As the frontend, I want one read endpoint in the agreed format so that I can replace the mock data without changing components.

Acceptance criteria:
1. `GET /status` SHALL go through API Gateway (HTTP API) to a Python 3.12 Lambda.
2. The response SHALL match `.kiro/steering/api-format.md` exactly: top-level `services` and `events`, with the field names and value rules defined there.
3. For each configured service, `services` SHALL contain one entry with `id`, `name`, `status`, `responseMs`, and `lastChecked`.
4. WHEN a configured service has a stored record THEN its entry SHALL use the stored `status`, `responseMs`, and `lastChecked`, with `name` taken from the configuration list.
5. WHEN a configured service has no stored record THEN its entry SHALL have `status` `unknown`, `responseMs` `null`, and `lastChecked` `null`.
6. Stored records for services that are not in the configuration list SHALL NOT be returned.
7. `events` SHALL contain at most the 20 most recent events, newest first by `at`.
8. The 20 most recent events SHALL be fetched with a `Query`, not a `Scan`, and the read cost SHALL NOT grow with the total number of stored events.
9. Counters and internal attributes (keys, version, TTL) SHALL NOT appear anywhere in the response.
10. All status values in the response SHALL be lowercase, and all timestamps SHALL be UTC ISO 8601 with a `Z` suffix.
11. The response SHALL be `200` with `Content-Type: application/json`.
12. WHEN the reader fails (DynamoDB error, invalid configuration, invalid stored data) THEN it SHALL return a `5xx` response in the error format from `api-format.md`, with no stack traces or AWS identifiers. It SHALL log the cause without logging secrets.

## Requirement 7: Throttling and exposure
**User story:** As an operator, I want the public endpoint limited so that abuse cannot run up cost.

Acceptance criteria:
1. The API SHALL have rate and burst throttling limits, set through SAM parameters with low defaults.
2. Only `GET /status` SHALL be exposed. Other routes and methods SHALL return API Gateway's default 404 or 405 response and SHALL NOT invoke the Lambda.
3. CORS SHALL be disabled unless an allowed frontend origin is passed as a parameter. CORS SHALL NOT allow `*`.

## Requirement 8: IAM
**User story:** As an operator, I want each role to hold only the permissions it needs so that a bug in one function cannot damage data.

Acceptance criteria:
1. The reader's role SHALL allow only `dynamodb:GetItem` and `dynamodb:Query` on this table. It SHALL NOT allow writes or `Scan`.
2. The template SHALL define a separate writer role for the future checker, allowing only the actions the write rules in design.md need (`GetItem`, `PutItem`, `UpdateItem`, and nothing broader) on this table.
3. No policy SHALL use a `*` resource for DynamoDB actions or grant administrator access.
4. Both roles SHALL be able to write their own CloudWatch Logs and nothing else outside the table.

## Requirement 9: Infrastructure as code and secrets
1. Everything SHALL be defined in an AWS SAM template at `backend/template.yaml`.
2. The template SHALL NOT contain secrets, account IDs, or hard-coded ARNs. Values that vary by environment SHALL be parameters.
3. No VPC or NAT Gateway SHALL be created.
4. No resources SHALL be deployed or created as part of this work.

## Requirement 10: Tests
1. pytest unit tests SHALL cover the response shaping logic without AWS access or network calls.
2. The tests SHALL cover:
   - an `unknown` default with `null` `responseMs` and `lastChecked` when no record exists,
   - newest-first event ordering,
   - the 20-event limit,
   - lowercase status names,
   - UTC ISO 8601 timestamps with a `Z` suffix,
   - counters and internal attributes not appearing in the response.
3. The tests SHALL run with `python -m pytest` from `backend/` using only the standard library and pytest.

## Requirement 11: Frontend error handling (follow-up, frontend spec)
**User story:** As a viewer, I want to know when the data is out of date so that I don't trust a stale pet.

This requirement is documented here because it depends on this API's error behavior. It is implemented in the frontend spec that replaces the mock data, **not** in this spec's tasks.

Acceptance criteria:
1. WHEN `/status` fails (network error, non-2xx including 429, or invalid JSON) THEN the UI SHALL show an error state and keep the last good data visible, marked as stale with the time it was last updated.
2. The error state SHALL be a UI state, not a new status value.
3. WHEN the response is 429 THEN the UI SHALL back off its polling interval, up to 60 seconds.
4. WHEN an event has a `serviceId` that is not in the current service list THEN the UI SHALL still show the event, labeled with the raw `serviceId`.

## Decisions
1. **Events for removed services** are still returned until they expire. The frontend SHALL tolerate an event `serviceId` that is not in the current configuration.
2. **Invalid stored status**: the reader SHALL return a `5xx` and log the problem with the service id (fail loudly). The writer SHALL validate the status before writing. design.md describes the frontend error state.
