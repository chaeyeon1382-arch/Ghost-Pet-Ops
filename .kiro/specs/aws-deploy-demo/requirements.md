# Requirements: AWS deployment structure, frontend hosting and demo

Steering documents in `.kiro/steering/` apply. Related specs: `state-store-read-api` (backend, design 6.3 and 7) and `frontend-status-api` (frontend).

## Introduction

The backend stack (table, `GET /status`, status Lambda, checker role and log group) and the frontend `/status` client already exist.
This spec adds what is missing to run the whole system on AWS: the checker Lambda resource and its schedule, a private S3 + CloudFront
frontend that serves `/status` from the same domain, a small controllable demo target, a cost alert, and the documentation for a demo.

The checker's code (`checker_app.py`, `checker_writer.py`, `decision.py`, `checker_items.py`, `http_check.py`) is written by a teammate.
This spec does not create or change those files. Values that depend on that code are marked **TBD (미확정)** and are not assumed.

**Out of scope:** the checker's code, any real AWS deployment or other cost-incurring action (these need separate approval),
custom domains, AWS WAF, NAT Gateway, authentication, and failure injection into the real API beyond the demo target.

## Glossary

- **Backend stack**: `backend/template.yaml`.
- **Frontend stack**: `frontend/template.yaml` (new): S3 bucket, CloudFront distribution.
- **Demo stack**: `demo/template.yaml` (new): the controllable demo target. Deleted after the demo.
- **Demo target**: a small Lambda behind a Function URL, whose behavior is chosen by the `MODE` environment variable.
- **TBD (미확정)**: a value that the checker author must confirm. It is shown as TBD, never filled in by guess.

## Requirement 1: Checker Lambda in the backend stack

**User story:** As the team, I want the checker Lambda defined in the template so that it can be deployed with the rest of the backend.

1. The backend stack SHALL define one `AWS::Serverless::Function` for the checker with `CodeUri: src/`, runtime `python3.12` and `arm64`, like the status function.
2. The function SHALL use the existing `CheckerFunctionRole` and the existing `CheckerFunctionLogGroup` (`LoggingConfig.LogGroup`). No second role or log group SHALL be created.
3. The handler SHALL be `checker_app.handler` (**CONFIRMED** by the checker author). The file `backend/src/checker_app.py` does not exist yet. The template SHALL NOT be deployed before it exists on `main` (Requirement 15.5).
4. The function SHALL have the environment variables `TABLE_NAME` and `EVENT_RETENTION_DAYS` (**CONFIRMED**, default 7 days). Service URLs come from `ghostpet/services.json`, not from the environment. Any additional variable is **TBD (미확정)**, and the template SHALL NOT set a variable that the checker author has not confirmed.
5. WHEN the checker author confirms an additional variable THEN the template SHALL be updated with that value only.
6. The template SHALL NOT contain account IDs, hard-coded ARNs or secrets.

## Requirement 2: Least-privilege IAM

**User story:** As the owner, I want each role to do only what it needs so that a mistake or compromise has a small effect.

1. `CheckerFunctionRole` SHALL keep exactly `dynamodb:GetItem`, `dynamodb:PutItem` and `dynamodb:UpdateItem` on the state table ARN, plus `logs:CreateLogStream` and `logs:PutLogEvents` on its own log group. No action SHALL be added.
2. The role that EventBridge Scheduler assumes SHALL allow `lambda:InvokeFunction` on the checker function only, and its trust policy SHALL name only `scheduler.amazonaws.com`.
3. No policy in any new stack SHALL use `Resource: "*"` except where an AWS service action cannot be scoped. Each such case SHALL be listed with a reason in design.md.
4. The demo target's role SHALL have no permissions except writing to its own log group. It SHALL NOT have access to the state table.
5. The frontend bucket SHALL be readable only by the CloudFront distribution that serves it (origin access control scoped to the distribution ARN).

## Requirement 3: One-minute schedule

**User story:** As a viewer, I want fresh data so that the pet reflects the site within about a minute.

1. The backend stack SHALL define an EventBridge Scheduler schedule that invokes the checker function every 1 minute.
2. The schedule SHALL use no flexible time window.
3. The retry policy was chosen in design.md 4.1 and 4.2: no Lambda asynchronous retries, and a Scheduler retry limited to 60 s. Overlapping invocations are already safe (see requirement 4.5).
4. The invocation SHALL send the empty JSON object `{}` (**CONFIRMED** by the checker author). Whether the handler reads it is checked before deployment (design P-5).
5. WHEN the stack is deleted THEN the schedule SHALL be deleted with it.

## Requirement 4: Runtime settings

**User story:** As the owner, I want sensible limits so that a slow site or a bug cannot cause surprise cost.

1. `Timeout`: the final value is **TBD (미확정)** until agreed with the checker author. design.md SHALL explain how it depends on the checker (sequential or parallel checks, DNS time, per-socket-operation limits, DynamoDB time) and give the formula. The formula SHALL NOT be "services x 5 s", because the 5 s HTTP limit is per socket operation and does not cover DNS. The owner approved 150 s as an initial ceiling, to be lowered after the first day of measurements. The template parameter has no default until the author agrees.
2. `MemorySize` SHALL be **TBD (미확정)**. design.md MAY propose a starting value, marked as a proposal that needs a measurement.
3. The log retention SHALL reuse the existing `LogRetentionDays` parameter (default 14).
4. `ReservedConcurrentExecutions` SHALL NOT be set. Reasons: a new account may not be able to reserve any concurrency, and it is not needed.
5. design.md SHALL explain why overlapping invocations are safe without a concurrency limit. The explanation SHALL cite `state-store-read-api/design.md` section 4: conditional writes on `version` and `lastChecked`, the conditional event `Put`, the `ClientRequestToken`, and the table in 4.5.
6. The function SHALL NOT run inside a VPC.

## Requirement 5: Private static hosting

**User story:** As the owner, I want the site to be served only through CloudFront so that the bucket is never public.

1. The frontend stack SHALL create an S3 bucket with all four public-access blocks on, no static-website hosting, and encryption at rest.
2. The CloudFront distribution SHALL use origin access control to read the bucket. The bucket policy SHALL allow only that distribution.
3. The distribution SHALL use the default CloudFront certificate and domain. It SHALL NOT use a custom domain.
4. Viewers SHALL be redirected to HTTPS. The distribution SHALL set a minimum TLS version supported by the default certificate and SHALL attach a security-headers response policy.
5. The distribution SHALL NOT define custom error responses that replace a status with `index.html` (the app has no client-side routes, and such a rule would also rewrite `/status` errors, because custom error responses apply to the whole distribution).
6. Hashed files under `/assets/` MAY be cached for a long time. `index.html` SHALL NOT be cached for long. Requirement 7 states how `index.html` is refreshed.
7. Access logging SHALL be off by default, to avoid extra storage cost.

## Requirement 6: `/status` through CloudFront

**User story:** As a viewer, I want `/status` on the same domain so that the browser needs no CORS and the API is protected by the edge cache.

1. The distribution SHALL have a cache behavior for the exact path `/status` with the HTTP API (`ApiDomain` from the backend stack) as an HTTPS-only origin.
2. The behavior SHALL allow only `GET` and `HEAD`.
3. The origin request policy SHALL be one that does not forward the viewer `Host` header (API Gateway needs its own). The cache policy SHALL use minimum TTL 0, default TTL 5 s and maximum TTL 10 s, with no query strings, headers or cookies in the cache key.
4. The backend stack's `AllowedOrigin` SHALL stay empty, so no CORS headers are used.
5. `ApiDomain` SHALL be passed to the frontend stack as a parameter. CloudFormation exports and imports SHALL NOT be used.
6. WHEN the origin returns a 5xx error THEN CloudFront SHALL NOT keep that error for long. The error caching time SHALL be set explicitly and justified in design.md.
7. The frontend ignores the body of a non-2xx response (`frontend-status-api` requirement 4.2). The distribution SHALL NOT replace `/status` error bodies with HTML.
8. All other paths SHALL be served from the bucket.
9. design.md SHALL record that the direct API URL stays reachable, and that stage throttling (`ThrottleRateLimit`, `ThrottleBurstLimit`) still applies to it.

## Requirement 7: Frontend build and deploy order

**User story:** As the deployer, I want one clear order so that nothing is built or uploaded against missing resources.

1. The frontend SHALL be built with `VITE_DATA_SOURCE=api` and the relative path `/status`. No API host SHALL be baked into the bundle. The existing CI check for `execute-api` and `amazonaws` in `dist/` SHALL keep passing.
2. The documented deployment order SHALL be:
   1. Demo stack (Requirement 8).
   2. Update `services.json` with the demo URL and the real sites, in a pull request (Requirement 9).
   3. Backend stack. Note `ApiDomain`.
   4. Frontend stack with `ApiDomain`.
   5. Build the frontend, upload `dist/` to the bucket, and invalidate `/index.html`.
3. The documentation SHALL state that step 5 does not need any stack output at build time, because the path is relative.
4. The documentation SHALL give the upload and invalidation commands with placeholders only (no real bucket, distribution id or account id).
5. No deployment script that holds account-specific values SHALL be committed.

## Requirement 8: Demo target

**User story:** As the presenter, I want to create normal, slow, down and recovery states on demand so that the demo shows every pet mood.

1. The demo stack SHALL define a small Lambda with a Function URL that anyone can call without credentials, and nothing else that is publicly reachable.
2. The Lambda SHALL read `MODE` from its environment. Values: `ok` (HTTP 200, immediately), `slow` (HTTP 200 after a delay between 2 s and 5 s, so that it is slow but not a timeout), `down` (HTTP 503). Any other value SHALL behave as `ok`.
3. The maximum delay SHALL be fixed in code and be shorter than the checker's 5 s timeout (`TIMEOUT_MS`). A request SHALL NOT be able to choose its own delay.
4. The presenter SHALL change the state by editing the `MODE` environment variable in the Lambda console. No code change or redeploy SHALL be needed.
5. The response body SHALL be short, fixed text. The Lambda SHALL NOT log request headers or bodies.
6. The Lambda SHALL use `python3.12`, `arm64`, the smallest memory size, a timeout longer than the maximum delay, and a log retention of 7 days or less.
7. design.md SHALL document (with a link to AWS documentation) whether a Function URL hostname contains the AWS account ID. IF it does THEN the demo URL SHALL NOT be committed, and the decision SHALL be taken again with the owner.
8. design.md SHALL include a timeline table for each change of `MODE`, computed from `petState.ts` thresholds (2 slow checks, 3 failures, the first success after an outage, then 2 fast checks counting that first success) and the 1-minute schedule. Recovery to `healthy` takes 2 checks.
9. The demo stack SHALL be deletable on its own, and the documentation SHALL say to delete it after the demo.

## Requirement 9: Monitored services

**User story:** As the presenter, I want real sites and a controllable site in the list so that the dashboard shows real and staged data together.

1. `services.json` SHALL contain: two large public sites, and one or two demo target entries. The entries SHALL follow the existing validation rules (https, public DNS name, unique `id`, at most 25 entries, no tokens in URLs).
2. The placeholder `example.com` entries SHALL be removed, because most of their paths return 404 and would show as `down`.
3. Each real site SHALL answer the checked URL directly with a 2xx or 3xx response. The checker does not follow redirects, and a 3xx response counts as success (`petState.ts` `isFailure`). This is **CONFIRMED** by the checker author and visible in `http_check.py` and `decision.py`.
4. design.md SHALL list candidate sites, say that their normal response time has to be measured from the checker's AWS region before deployment, and say how (a command run from that region, after approval). A measurement from a different network SHALL NOT be treated as final.
5. Response times above 2000 ms count as slow (`SLOW_THRESHOLD_MS`). IF a candidate's normal response time is close to that value THEN it SHALL NOT be chosen.
6. design.md SHALL note that a site may block requests from cloud IP ranges (for example with a 403), which would show as `down`.
7. Monitoring SHALL use one request per minute per site, with no extra headers and no credentials.
8. WHEN the demo target URL is known THEN it SHALL be committed in `services.json` in a pull request (owner decision). The pull request SHALL NOT contain account ids, access keys or email addresses.

## Requirement 10: Cost control

**User story:** As the owner, I want a small, predictable bill so that the project cannot surprise me.

1. The stacks SHALL NOT use AWS WAF, NAT Gateway, a custom domain, a VPC endpoint, Provisioned Concurrency, provisioned DynamoDB capacity or any other fixed hourly cost.
2. A separate small template `ops/budget.yaml` (owner approved) SHALL define an `AWS::Budgets::Budget` monthly cost budget. The limit SHALL be a parameter, with a default of 10 USD.
3. The budget SHALL send email alerts at 50% of actual spend, 100% of actual spend and 100% of forecast spend.
4. The notification email SHALL be a required parameter with no default value. It SHALL NOT be written in any committed file.
5. design.md SHALL contain a monthly cost estimate per service (Lambda, Scheduler, DynamoDB, API Gateway, CloudFront, S3, CloudWatch Logs, Budgets), with the price source and its date, and a worst-case estimate for the demo target.
6. design.md SHALL state the limits of Budgets: it is account-level, and its data is delayed by several hours.
7. design.md SHALL list the resources and the order for removing everything after the demo.

## Requirement 11: No secrets or account details in the repository

**User story:** As the owner, I want nothing sensitive to reach GitHub.

1. No committed file (templates, README, docs, screenshots, spec files) SHALL contain an AWS account id, access key, session token, email address, bucket name that includes an account id, or a distribution id of a real deployment.
2. Deployment settings (`samconfig.toml`) SHALL stay git-ignored. The existing `.gitignore` rules SHALL stay.
3. Screenshots SHALL be checked before commit. The browser address bar and any AWS console part SHALL be cropped or not captured.
4. Commands in documentation SHALL use placeholders such as `<bucket>`, `<distribution-id>`, `<region>`.

## Requirement 12: Documentation, demo checks and screenshots

**User story:** As a reviewer, I want to follow written steps and see the result so that I can check that the system works.

1. `backend/README.md` and `README.md` SHALL describe the architecture, deployment order, parameters, cost summary, budget setup and removal steps. They SHALL replace the "checker does not exist yet" notes only when the checker is deployed.
2. A demo script SHALL list the steps: set `MODE` for each state, the expected pet state, and the expected time until it appears (from requirement 8.8).
3. A check list SHALL cover the mobile and desktop layout: widths 360 px, 768 px and 1280 px, portrait and landscape on mobile, the five pet states, the "NO SIGNAL" overlay, text that does not overflow, and tap targets.
4. A screenshot plan SHALL list each shot (state, viewport, file name) under `docs/screenshots/`. Screenshots SHALL be taken only from a deployed demo that the owner approved.
5. The documentation SHALL state which values come from the checker author and are still TBD.

## Requirement 13: Items for the checker author (confirmation list)

**User story:** As the infrastructure owner, I want the open questions in one place so that nothing is guessed.

design.md and tasks.md SHALL keep this list, with a status for each item (TBD or confirmed, with the answer and date):

1. Handler name. **CONFIRMED**: `checker_app.handler`.
2. Environment variables. **CONFIRMED**: `TABLE_NAME`, `EVENT_RETENTION_DAYS`. Any additional variable: **TBD (미확정)**.
3. Whether services are checked one after another or in parallel.
4. Timeout.
5. Memory size (or a measurement plan).
6. Payload. **CONFIRMED**: `{}`. Whether the handler reads it: checked before deployment.
7. Whether 3xx is treated as success and not followed. The owner decided: not followed, counted as success (`petState.ts` and `security.md`). **CONFIRMED**: `http_check.py` and `decision.py` do this.
8. Schedule retry behavior that the checker expects. **TBD (미확정)**. Proposal in design.md 4.1 and 4.2.
9. (C-1) Whether `checker_app.py` catches `BlockedAddressError` per service. **TBD (미확정)**.
10. (C-2) DynamoDB client timeouts and retries. **TBD (미확정)**.
11. (C-3) Protection against a hanging service starving later services. **TBD (미확정)**.
12. (C-4) Use of only the first `getaddrinfo` result (IPv6 risk). **TBD (미확정)**.
13. (C-5) Timing of the Draft PR for `checker_writer.py` and `checker_app.py`. **TBD (미확정)**.

## Requirement 14: Protected files and CI

**User story:** As the team, I want to avoid conflicts with the teammate's work.

1. This work SHALL NOT create, edit or delete `decision.py`, `checker_items.py`, `http_check.py`, `checker_writer.py` or `checker_app.py`. The first three exist on `main`. The last two do not exist yet and are written by the checker author.
2. It SHALL NOT change `src/domain/petState.ts` or any file in `src/` of the frontend.
3. `.github/workflows/ci.yml` SHALL run `sam validate --lint` for `frontend/template.yaml` and `demo/template.yaml` in addition to the backend template, without credentials. `ops/budget.yaml` is not validated by CI until the owner approves it. The existing jobs and permissions SHALL NOT change otherwise.
4. WHEN a template has a required parameter without a default THEN `sam validate --lint` SHALL still pass.
5. Templates SHALL be validated in CI only. A successful validation does not prove that a stack deploys, and the documentation SHALL say so.

## Requirement 15: Approval gates

1. Writing requirements, design and tasks each SHALL wait for the owner's approval.
2. No template or code SHALL be written before the tasks are approved.
3. No `sam deploy`, `aws` command that creates, changes or deletes resources, or any other action that can cost money SHALL be run without a separate approval.
4. Work SHALL happen on branch `feature/aws-deploy-demo`, and not on `main`.
5. **No deployment of the backend stack or the checker before the checker author's pull request (`checker_writer.py` and `checker_app.py`) is merged into `main`.** Before any deployment, the pre-deploy reconciliation in design.md section 12 (P-0 to P-12) SHALL pass.
6. The checker schedule SHALL start `DISABLED`. Enabling it is a separate, approved step after one manual invocation has been checked.
