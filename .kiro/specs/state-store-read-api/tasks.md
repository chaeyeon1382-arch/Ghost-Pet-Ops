# Tasks: State Store and Read API

Each task ends with `cd backend && python -m pytest` passing. Nothing is deployed and the CI workflow is not changed.
Requirement numbers refer to `requirements.md` and section numbers to `design.md`.

- [x] 1. Pure response shaping and its tests
  - Create `backend/src/ghostpet/__init__.py` and the reader-side part of `backend/src/ghostpet/model.py`:
    `STATUSES`, `format_ts`, `parse_ts`, `InvalidStoredData(service_id, field)`, `validate_service_record`, `validate_event`.
  - Create `backend/src/ghostpet/status_response.py` with `build_status_response(services, records, events, now)`.
    It is pure: no boto3, no clock, and no environment access.
  - Create `backend/tests/conftest.py` (adds `src/` to `sys.path`), `backend/tests/test_status_response.py`, and `backend/requirements-dev.txt` (pytest only).
  - Tests: an `unknown` default with `null` `responseMs` and `lastChecked`, config order kept, newest-first ordering with a tie on `at`, the 20-event limit (25 in, 20 out), lowercase statuses and rejection of `"Healthy"` and `"broken"` with the service id, the UTC `Z` timestamp format and rejection of malformed values, exact output keys with no counters, `pk`, `sk`, `version` or `expiresAt`, records for unconfigured services ignored, events for removed services kept, expired events dropped, and `Decimal` converted to `int`.
  - Requirements 6.3–6.10, 10. Design 2.3, 2.4 (expired events), 5.1 step 4.

- [x] 2. Writer helpers in the shared model
  - Add to `ghostpet/model.py`: `SERVICES_PK`, `EVENTS_PK`, `service_sk(id)`, `event_sk(at, service_id)`, `expires_at(at, retention_days)`, and `idempotency_token(service_id, checked_at)`.
  - Create `backend/tests/test_model.py`:
    - key formats,
    - the `format_ts`/`parse_ts` round trip,
    - `expires_at` with 7 days adds 604,800 s,
    - the token is stable, 36 characters, and differs per service and per time,
    - the validators reject negative counters and identical `from`/`to`.
  - Requirements 3, 4.3. Design 2.1, 2.2, 4.4, 8.1.

- [x] 3. Service configuration
  - Create `backend/src/ghostpet/services.json` with placeholder entries using `https://example.com/...` URLs only, and no real endpoints.
  - Create `backend/src/ghostpet/config.py`. `load_services(path=None)` returns a list of `Service(id, name, url)` and raises `ConfigError` on invalid input.
  - Create `backend/tests/test_config.py`. It must reject an empty list, more than 25 entries, a duplicate id, an id with `#` or uppercase letters, a missing or overlong name, a non-https URL, and a URL with user info. It must accept the bundled file.
  - Requirement 5. Design 3.

- [x] 4. Status Lambda handler
  - Create `backend/src/status_app.py`:
    - Load the config at cold start and store any error instead of raising it.
    - Run two `Query` calls with projections. The services query follows `LastEvaluatedKey`. The events query uses `ScanIndexForward=False` and `Limit=20`.
    - Shape the response, and return `200` with `Content-Type: application/json` and `Cache-Control: public, max-age=5`.
    - Return `500` with the error body from `api-format.md`, and log one JSON line per request.
    - The table object is injectable for tests. The boto3 resource is created lazily, only in Lambda.
  - Create `backend/tests/test_status_app.py` using a fake table, with no boto3 calls. It must cover:
    - a 200 response with the right headers and body,
    - the arguments of both queries (key, no `Scan`, `Limit=20`, newest first),
    - 500 for invalid config, invalid stored data, and a DynamoDB error,
    - error bodies containing no stack trace, exception text or AWS ids,
    - logs containing the service id for invalid data.
  - Requirements 1.4, 6.1–6.12. Design 5.1, 5.2.

- [x] 5. SAM template
  - Create `backend/template.yaml` per design 7:
    - Parameters (throttle rate 20 and burst 50, `AllowedOrigin` with a pattern that blocks `*`, `EventRetentionDays` 7, `LogRetentionDays` 14).
    - `StateTable` (on-demand, `pk`/`sk`, TTL on `expiresAt`).
    - `StatusApi` (HTTP API, stage throttling, CORS only when `AllowedOrigin` is set).
    - Status log group, `StatusFunctionRole` (`dynamodb:Query` on the table, logs on its own group), and `StatusFunction` (`CodeUri: src/`, python3.12, arm64, `GET /status`).
    - `CheckerFunctionRole` (`GetItem`, `PutItem`, `UpdateItem` on the table, logs on the future checker group).
    - Outputs.
  - Create the empty `backend/src/requirements.txt`.
  - Add a `backend` job to `.github/workflows/ci.yml` (requested during review). It runs Python 3.12, pytest in `backend/`, and `sam validate --lint`, uses no credentials, and keeps permissions read-only. The Node job is unchanged.
  - Verified on a GitHub runner from a temporary scratch branch, now deleted, with a one-off script that is not committed:
    - the YAML parses with CloudFormation tags,
    - the role actions match design 4.6 and 7,
    - no `*`, account IDs or hard-coded ARNs,
    - no VPC or NAT resources,
    - throttling comes from parameters,
    - the rendered SAM transform has no CORS block by default,
    - `sam build` packages `ghostpet/`.
  - Requirements 1–2, 4.4–4.5, 7, 8, 9.

- [x] 6. Docs and housekeeping
  - Add `backend/README.md`. It covers how to run the tests, the deploy order from design 6.3, the AWS Budgets recommendation (design 6.2), the `sam validate --lint` step before the first deploy, and a pointer to design section 4 for the checker author.
  - Add `__pycache__/` and `.pytest_cache/` to `.gitignore`. Add a backend line to the root `README.md`.
  - Update design 3, 4.1, 7 and 10 to match what was built: host rules, the SSRF check at request time, whole-block CORS `!If`, and validation in CI.
  - `structure.md` is already updated in the design step. Check that it still matches the files created.

- [x] 7. Review and pull request
  - Run the full pytest suite and the template checks from task 5 once more. Check that no secrets are present.
  - Commit on `feature/state-store-read-api`, push, and open a PR into `main` that lists the known gaps.

## Follow-ups (not in this spec)
- Frontend spec: requirement 11 (error state, stale marker, 429 backoff, unknown `serviceId`) and replacing the mock data with `/status`.
- Checker spec (teammate): `ghostpet/decision.py`, `src/checker_app.py`, EventBridge Scheduler, and the write contract in design section 4.
- Frontend hosting spec: S3 and CloudFront with the `/status` behavior from design 6.3.
- Account setup: the AWS Budgets alert from design 6.2.
