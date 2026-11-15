# Requirements: Ghost Pet Ops MVP

## Introduction
Ghost Pet Ops shows a retro ghost pet whose mood reflects the health of a monitored web service.
This MVP delivers the frontend with mock data and the state decision logic.
AWS infrastructure, authentication, and AI features are out of scope for this phase.

## Requirement 1: Classify each check
**User story:** As an operator, I want each health check classified as success or failure so that state changes rest on clear rules.

Acceptance criteria:
1. WHEN a check times out (5 seconds) THEN the system SHALL count it as a failure.
2. WHEN a check cannot connect (`statusCode` is null) THEN the system SHALL count it as a failure.
3. WHEN a check returns a 4xx or 5xx status THEN the system SHALL count it as a failure.
4. OTHERWISE the system SHALL count the check as a success.

## Requirement 2: Decide the pet state
**User story:** As an operator, I want the pet state to change only on sustained signals so that one blip does not cause a false alarm.

Acceptance criteria:
1. WHEN the state is healthy AND two consecutive successful checks are slower than 2000 ms THEN the state SHALL become slow.
2. WHEN the state is slow AND two consecutive successful checks take 2000 ms or less THEN the state SHALL become healthy.
3. WHEN three consecutive checks fail THEN the state SHALL become down, from any state.
4. WHEN one or two consecutive checks fail AND the state is not recovering THEN the state SHALL stay the same.
5. WHEN the state is down AND one check succeeds THEN the state SHALL become recovering.
6. WHEN the state is recovering AND one check succeeds THEN the state SHALL become healthy.
7. WHEN the state is recovering AND one check fails THEN the state SHALL become down.
8. WHEN a streak is interrupted (a success after failures, a fast check after slow ones, or the reverse) THEN that streak SHALL reset to zero.
9. The decision logic SHALL be a pure module with no dependency on React, the network, or the clock.

## Requirement 3: Show the pet
**User story:** As a viewer, I want to see the pet, its status, and recent state changes at a glance.

Acceptance criteria:
1. The UI SHALL render a distinct pet appearance and caption for each of the four states.
2. The UI SHALL show the service name, status, last response time, and last check time.
3. The UI SHALL list state-change events, newest first.
4. The UI SHALL read data only through the `StatusResponse` format in `.kiro/steering/api-format.md`.
5. Animations SHALL be disabled when the user prefers reduced motion.

## Requirement 4: Demo controls
**User story:** As a presenter, I want buttons that switch between states so that I can demo without a real outage.

Acceptance criteria:
1. The UI SHALL show one button per state: healthy, slow, down, recovering.
2. WHEN a button is pressed THEN the app SHALL replay a scripted check sequence through the real decision logic and display the result.
3. The selected button SHALL be marked with `aria-pressed`.

## Requirement 5: Tests
1. Tests SHALL cover the seven agreed scenarios (see design.md, Test plan).
2. `npm test` SHALL run the tests once, without watch mode.
