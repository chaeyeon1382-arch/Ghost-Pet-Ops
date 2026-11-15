# Requirements: Ghost Pet Ops MVP

## Introduction
Ghost Pet Ops shows a retro ghost pet whose mood reflects the health of a monitored web service.
This MVP delivers the frontend with mock data and the status decision logic.
AWS infrastructure, authentication, and AI features are out of scope for this phase.

## Glossary
- **Status**: one of `unknown`, `healthy`, `slow`, `down`, `recovering` (always lowercase).
- **Failure**: a check that timed out (5 seconds), could not connect, or returned 4xx/5xx.
- **Success**: any check that is not a failure, including 3xx.
- **Fast success**: a success with `responseMs <= 2000`.
- **Slow success**: a success with `responseMs > 2000`.
- **Counters**: `consecutiveFailures`, `consecutiveSlow`, `consecutiveFast`. They persist between checker runs.

## Requirement 1: Classify each check
**User story:** As an operator, I want each health check classified so that status changes rest on clear rules.

Acceptance criteria:
1. WHEN a check times out (5 seconds) THEN the system SHALL classify it as a failure.
2. WHEN a check cannot connect (`statusCode` is null) THEN the system SHALL classify it as a failure.
3. WHEN a check returns a 4xx or 5xx status THEN the system SHALL classify it as a failure.
4. WHEN a check returns any other status, including 3xx, THEN the system SHALL classify it as a success.
5. WHEN a success has `responseMs <= 2000` THEN it SHALL be a fast success. Exactly 2000 ms is fast.
6. WHEN a success has `responseMs > 2000` THEN it SHALL be a slow success. 2001 ms is slow.

## Requirement 2: Update counters
**User story:** As an operator, I want streaks tracked explicitly so that the checker can resume from stored state on every run.

Acceptance criteria:
1. WHEN a check is a failure THEN `consecutiveFailures` SHALL increase by 1 AND `consecutiveSlow` and `consecutiveFast` SHALL reset to 0.
2. WHEN a check is a fast success THEN `consecutiveFast` SHALL increase by 1 AND `consecutiveFailures` and `consecutiveSlow` SHALL reset to 0.
3. WHEN a check is a slow success THEN `consecutiveSlow` SHALL increase by 1 AND `consecutiveFailures` and `consecutiveFast` SHALL reset to 0.
4. Counters SHALL be updated before the status rules are applied.

## Requirement 3: Decide the status
**User story:** As an operator, I want the status to change only on sustained signals so that one blip does not cause a false alarm.

Acceptance criteria:
1. Before any check has run, the status SHALL be `unknown`.
2. WHEN the status is `unknown` AND a check is a fast success THEN the status SHALL become `healthy`.
3. WHEN the status is `unknown` AND `consecutiveSlow >= 2` THEN the status SHALL become `slow`.
4. WHEN the status is `unknown` AND `consecutiveFailures >= 3` THEN the status SHALL become `down`.
5. WHEN the status is `unknown` AND none of 3.2 to 3.4 applies THEN the status SHALL stay `unknown`.
6. WHEN the status is `healthy` AND `consecutiveSlow >= 2` THEN the status SHALL become `slow`.
7. WHEN the status is `slow` AND `consecutiveFast >= 2` THEN the status SHALL become `healthy`.
8. WHEN the status is `healthy` or `slow` AND `consecutiveFailures >= 3` THEN the status SHALL become `down`.
9. WHEN the status is `healthy` or `slow` AND `consecutiveFailures` is 1 or 2 THEN the status SHALL stay the same.
10. WHEN the status is `down` AND a check is any success THEN the status SHALL become `recovering`.
11. WHEN the status is `down` AND a check is a failure THEN the status SHALL stay `down`.
12. WHEN the status is `recovering` AND `consecutiveFast >= 2` THEN the status SHALL become `healthy`. The first success after the outage counts toward this if it is fast.
13. WHEN the status is `recovering` AND `consecutiveSlow >= 2` THEN the status SHALL become `slow`. The first success after the outage counts toward this if it is slow.
14. WHEN the status is `recovering` AND a check is a slow success AND `consecutiveSlow < 2` THEN the status SHALL stay `recovering` (and `consecutiveFast` resets per 2.3).
15. WHEN the status is `recovering` AND a check is a failure THEN the status SHALL become `down` immediately.
16. The decision logic SHALL be a pure module with no dependency on React, the network, or the clock.

## Requirement 4: Record events
**User story:** As a viewer, I want a short history of status changes so that I can see what happened recently.

Acceptance criteria:
1. The system SHALL record an event only when the status changes, including the first change from `unknown`.
2. The system SHALL NOT record an event for a check that leaves the status unchanged.
3. Each event SHALL contain `serviceId`, `from`, `to`, `at` (UTC ISO 8601), and `note`.
4. `GET /status` SHALL return at most the 20 most recent events, newest first.

## Requirement 5: API response
**User story:** As a frontend developer, I want a stable response format so that mock data and the real API are interchangeable.

Acceptance criteria:
1. The response format SHALL match `.kiro/steering/api-format.md`.
2. `status`, `from`, and `to` SHALL be lowercase values from the status list, including `unknown`.
3. `lastChecked` and `at` SHALL be UTC ISO 8601 strings with a `Z` suffix.
4. WHEN no check has run for a service THEN `responseMs` and `lastChecked` SHALL be `null`. Both fields are nullable in the API format.
5. The counters SHALL be persisted for the checker but SHALL NOT appear in the `/status` response.

## Requirement 6: Show the pet
**User story:** As a viewer, I want to see the pet, its status, and recent status changes at a glance.

Acceptance criteria:
1. The UI SHALL render a distinct pet appearance and caption for each of the five statuses.
2. WHEN the status is `unknown` THEN the UI SHALL show a neutral "waiting" appearance.
3. The UI SHALL show the service name, status, last response time, and last check time. WHEN `responseMs` or `lastChecked` is `null` THEN the UI SHALL show "—" in its place.
4. The UI SHALL list events, newest first.
5. The UI SHALL read data only through the `StatusResponse` format.
6. Animations SHALL be disabled when the user prefers reduced motion.

## Requirement 7: Demo controls
**User story:** As a presenter, I want buttons that switch between statuses so that I can demo without a real outage.

Acceptance criteria:
1. The UI SHALL show one button per status: unknown, healthy, slow, down, recovering.
2. WHEN a button is pressed THEN the app SHALL replay a scripted check sequence through the real decision logic and display the result.
3. The selected button SHALL be marked with `aria-pressed`.

## Requirement 8: Tests
1. Tests SHALL cover every scenario in the design.md test plan.
2. `npm test` SHALL run the tests once, without watch mode.
