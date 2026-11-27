# Tasks: AWS deployment structure, frontend hosting and demo

Requirement numbers refer to `requirements.md` and section numbers to `design.md` in this folder.
Work happens on branch `feature/aws-deploy-demo`. Nothing in this list is done until the owner approves these tasks.

## Rules for every task

- **Files that are never changed:** `decision.py`, `checker_items.py`, `http_check.py`, `checker_writer.py`, `checker_app.py` and their tests. Also `src/` (frontend) and `petState.ts`.
- **No AWS action without approval.** Part A and Part B change files in the repository only. Part D deploys, and every step in it has its own approval. No `sam deploy`, no `aws` command that creates, changes or deletes a resource, and no other cost-incurring action runs before that approval (requirement 15.3).
- **Nothing sensitive in git:** no account id, key, email address, real bucket name or distribution id (requirement 11). Commands use placeholders.
- **Validation:** the sandbox has no SAM CLI and no cfn-lint. Templates are validated by the CI `sam validate --lint` steps (task B-6). A passing validation does not prove that a stack deploys.
- **Unknown values stay unknown:** where a task needs a value that is TBD (미확정), the task says so and waits for the answer. It does not guess.

## Hard conditions (apply to Part D)

1. **No deployment of the backend stack, the checker, or the schedule before the checker author's pull request, which adds `checker_writer.py` and `checker_app.py`, is merged into `main`.** (task D-0)
2. **Before any deployment, all pre-deploy checks P-0 to P-12 pass** (Part C). A failed check is settled with the checker author first.
3. **The schedule is created `DISABLED`.** A manual invocation is checked first. Then the schedule is enabled in a separate step (tasks D-6 and D-7). If this step is forgotten, the checker never runs and every service stays `unknown`, so the dashboard shows the waiting pet forever.

---

## Part A. Specification

- [x] A-1. Requirements written and approved.
- [x] A-2. Design written and approved (owner: schedule starts `DISABLED`, 150 s is the initial `Timeout` ceiling, `ops/budget.yaml` is a separate file, requirements are aligned with design section 15).
- [x] A-3. Owner approves this task list.
- [x] A-4. Commit the three spec files in one commit on `feature/aws-deploy-demo` (no code yet). Approval: owner approves A-3. Push and open a draft pull request only when the owner asks.

---

## Part B. Repository changes (no AWS access)

Order matters only where a task says so. Each task ends with the checks written in it. Open one pull request per logical group if the owner prefers smaller reviews.

- [x] B-1. Backend template: checker function, schedule, scheduler role
  - Edit `backend/template.yaml` (design 4):
    - Add parameters `CheckerMemorySize` (Number, default 128), `CheckerTimeoutSeconds` (Number, **no default**, minimum 10, maximum 900), `CheckerScheduleState` (`ENABLED` or `DISABLED`, default `DISABLED`).
    - Add `CheckerFunction` with `Handler: checker_app.handler`, `python3.12`, `arm64`, `CodeUri: src/`, the existing `CheckerFunctionRole` and `CheckerFunctionLogGroup`, environment `TABLE_NAME` and `EVENT_RETENTION_DAYS` **only**, and `EventInvokeConfig` (`MaximumRetryAttempts: 0`, `MaximumEventAgeInSeconds: 60`).
    - Add `CheckerSchedulerRole` (trust `scheduler.amazonaws.com`, one action `lambda:InvokeFunction` on the checker function ARN) and `CheckerSchedule` (`rate(1 minute)`, flexible window off, `State` from the parameter, input `{}`, retry policy 2 attempts within 60 s).
    - Add outputs `CheckerFunctionName` and `CheckerScheduleName`. Update the comments and output descriptions that say "future" or "no function uses it yet".
  - Do **not** add `ReservedConcurrentExecutions`, a VPC, a layer, or any environment variable other than the two confirmed ones.
  - Existing resources and their behavior stay as they are. The checker role keeps exactly `GetItem`, `PutItem`, `UpdateItem` (requirement 2.1).
  - Check: read the diff for `Resource: "*"` (none allowed), literal account ids or ARNs (none allowed), and extra environment variables (none allowed). CI validation runs in B-6.
  - Requirements 1, 2, 3, 4. Design 4, 5.

- [x] B-2. Frontend stack template
  - Create `frontend/template.yaml` (design 6): private bucket with the four public-access blocks, encryption and owner-enforced ownership, origin access control, bucket policy limited to this distribution, `StatusCachePolicy` (0 / 5 / 10 s, empty cache key), the distribution with the S3 origin, the HTTPS-only API origin (parameter `ApiDomain` with a pattern check, no default), the exact `/status` behavior (GET and HEAD, `AllViewerExceptHostHeader`), `ErrorCachingMinTTL` of 1 for 500, 502, 503, 504 with no replacement page, a security-headers response policy, default CloudFront certificate, price class parameter, and outputs `DistributionDomainName` and `BucketName`.
  - Do **not** add custom error responses that replace a status with `index.html`, WAF, logging, a custom domain, or CloudFormation exports and imports.
  - Check: no literal account id in the bucket policy (use pseudo parameters), `AllowedOrigin` in the backend stays empty.
  - Requirements 5, 6. Design 6.

- [x] B-3. Demo stack
  - Create `demo/template.yaml` and `demo/src/app.py` (design 7): one function (`python3.12`, `arm64`, 128 MB, `Timeout: 10`), a role with only its own log-group permissions, a log group with 7 days retention, a Function URL with `AuthType: NONE`, parameter `InitialMode` (`ok`, `slow`, `down`, default `ok`).
  - Add an explicit `AWS::Lambda::Permission` for `lambda:InvokeFunction` with `InvokedViaFunctionUrl: true` (design 7.3). Without both statements the URL returns 403 and the demo target looks `down` in every mode.
  - `app.py` (about 20 lines): read `MODE`. `ok` (and any other value): 200 and a short fixed text. `slow`: wait a fixed **3.0 s** constant, then 200. `down`: 503 and a fixed text. No value from the request is used. Only the mode is logged, never headers or bodies.
  - Check: run the handler locally with Python for the three modes and one unknown value, and record the results in the pull request (this is a manual check, no test file is added unless the owner asks).
  - Requirements 2.4, 8. Design 7.

- [x] B-4. Budget stack
  - Create `ops/budget.yaml` (design 9.1): `AWS::Budgets::Budget`, monthly cost budget, `BudgetLimitUsd` default 10, notifications at actual 50%, actual 100% and forecasted 100%, parameter `NotificationEmail` (**no default**, address pattern check).
  - Check: the email address appears nowhere in the repository.
  - Requirement 10. Design 9.1.

- [ ] B-5. `services.json`: prepare, not final
  - **Blocked** until the candidate-site measurement (task D-3) and the demo URL (task D-4) exist. This task only records the list structure to use: two large public sites and one demo target entry (N = 3), `id`, `name` and `url` following `ghostpet/config.py` rules.
  - The `example.com` placeholder entries are removed in the final change (design 8). Their removal and the new entries go into **one** pull request (task D-5), not before, because the file is bundled into the Lambda.

- [x] B-6. CI: validate the new templates
  - Edit `.github/workflows/ci.yml` (owner approved): add three `sam validate --lint` steps to the existing `backend` job, or a new job with the same pinned SAM version and region (`us-east-1`), for `frontend/template.yaml`, `demo/template.yaml` and `ops/budget.yaml` (the owner added `ops/budget.yaml` in stage 2). No credentials. Same `permissions`. No other change.
  - If lint rejects a template because a parameter has no default, the fix is decided then (design 14.1). Do not weaken a secret parameter such as the email.
  - Check: the CI result on the pull request. This is the first real validation of B-1 to B-4.
  - Requirement 14.3 to 14.5. Design 14.1.

- [x] B-7. Documentation
  - Update `README.md` and `backend/README.md` (design 10): architecture, deployment order (design 11) with the hard conditions at the top, parameters (mark the ones without a default), cost summary, the budget step, removal steps (design 9.4), the CloudFront TLS note, the invalidation command (`/*`) replacing the `/index.html` text, and the demo target abuse note (design 7).
  - Replace "the checker does not exist yet" notes **only after** the checker is deployed (task D-9). Until then, add one sentence saying the checker is in progress.
  - Placeholders only: `<bucket>`, `<distribution-id>`, `<region>`.
  - Create `docs/demo.md`: demo script with the timeline table (design 7.2), the layout check list (widths 360, 768 and 1280 px, portrait and landscape on a phone, the five pet states, the NO SIGNAL overlay, overflow, tap targets, contrast), and the screenshot plan (file names `docs/screenshots/<number>-<state>-<viewport>.png`, no address bar, no AWS console).
  - Create `docs/screenshots/README.md` listing the planned shots and the rule that images are added only from an approved deployment.
  - Update `.kiro/steering/structure.md` with `frontend/`, `demo/`, `ops/` and `docs/`.
  - Requirements 7, 10, 11, 12.

- [x] B-8. Cost verification (pricing pages)
  - Look up and record, with the date and link, the prices and free tiers that design 9.2 marks UNVERIFIED: Lambda (arm64, free tier), EventBridge Scheduler, CloudFront (free tier, price class), S3, CloudWatch Logs, AWS Budgets. Replace the volume-only table in `design.md` 9.2 with the checked numbers, or state that a price could not be confirmed.
  - Requirement 10.5.

- [x] B-9. Verify the UNVERIFIED statements
  - Check each against AWS documentation and record the result in `design.md` (change the mark from UNVERIFIED to CONFIRMED, or fix the design):
    1. Scheduler role trust policy conditions `aws:SourceAccount` and `aws:SourceArn` (design 4.2).
    2. Managed policy ids: `CachingOptimized`, `AllViewerExceptHostHeader`, `SecurityHeadersPolicy` (design 6.1).
    3. Default-certificate minimum TLS behavior (design 6.1).
    4. Default error caching time and whether a 429 is cached (design 6.1). The 429 question is also tested after deployment (task D-10).
    5. Whether invalidating `/index.html` also clears `/` (design 6.3).
    6. Permissions that a public Function URL needs (`lambda:InvokeFunctionUrl`, and whether `lambda:InvokeFunction` is also required) and what SAM `FunctionUrlConfig` creates (design 7).
    7. Whether a new account can reserve concurrency (design 4.4). This is background only, since no reservation is used.
    8. The generation of the Function URL id and the account-id question (design 7.1). The decisive check is task D-4.
    9. Whether `AWS::Budgets::Budget` should be deployed in `us-east-1` (design 9.1).
  - Documentation pages that returned HTTP 403 in the earlier session are tried again, or another AWS documentation page is used.

- [ ] B-10. Commit and push
  - Commit each logical group (B-1 to B-4, B-6, B-7) with a clear message. Push `feature/aws-deploy-demo`. Open a pull request into `main` using `gh api` (REST), **only when the owner asks**. The pull request text lists: what is added, what is TBD (미확정), that nothing is deployed, and the hard conditions.
  - The pull request can merge before or after the checker author's pull request. Merging it does not deploy anything. The README states the hard conditions so nobody deploys by mistake.
  - **Owner decision (optional):** a CI guard that fails while `backend/src/checker_app.py` is missing would make the merge order enforceable. Recommended: **no CI guard**, because it would block this pull request on someone else's work, and deployment is manual and gated by D-0 anyway.

---

## Part C. Pre-deploy reconciliation (P-0 to P-12)

Run after the checker author's pull request is merged, and **again** if the checker code changes later. Each check produces a short written result (pass, or the difference found and who settles it). No file of the checker author is changed. A difference is sent to the author as a comment or note. These are read-only checks of the repository, so no AWS approval is needed.

- [ ] P-0. Gate: `checker_writer.py` and `checker_app.py` exist on `main` (after `git fetch`). If the author's work is still a Draft PR, stop here. The Draft PR can be read for early feedback, but it is not a deployment basis.
- [ ] P-1. Handler: `checker_app.py` defines `handler(event, context)`, and the template's `Handler` equals `checker_app.handler`.
- [ ] P-2. Environment variables: list every `os.environ` and `os.getenv` use in `checker_app.py`, `checker_writer.py` and everything they import. The template must set exactly those, and today it sets `TABLE_NAME` and `EVENT_RETENTION_DAYS` only. An extra variable is a TBD (미확정) that blocks the deploy until the author states its meaning and the template is updated (B-1 follow-up).
- [ ] P-3. Write contract: compare `checker_writer.py` with `state-store-read-api/design.md` section 4 (step 7 retry rules, 4.2 conditions, 4.3 transaction and cancellation reasons, 4.4 token). This check supports the "no concurrency limit" decision (design 4.4).
- [ ] P-4. IAM actions: list every DynamoDB call used. All must be covered by `GetItem`, `PutItem`, `UpdateItem`. If another action appears (`Query`, `Scan`, `ConditionCheckItem`), the role change goes through the owner first.
- [ ] P-5. Payload: the handler works with `{}` and does not depend on the event contents.
- [ ] P-6. URL allow-list: only URLs from `load_services()` reach `perform_check`.
- [ ] P-7. Blocked address: `BlockedAddressError` is handled per service (open item C-1), logged without secrets, and the run continues with the next service.
- [ ] P-8. Logging: no secret, header or response body is logged.
- [ ] P-9. Order and N: read how services are iterated (sequential or parallel, order, any rotation or wall-clock guard, open item C-3) and the number of services in the final `services.json`. Recalculate the `Timeout` with the formula in design 5.2.
- [ ] P-10. boto3 client timeouts and retries (open item C-2).
- [ ] P-11. `sam validate --lint` passes for all templates in CI, and `sam build` succeeds (run in CI or by the owner, since the sandbox has no SAM CLI).
- [ ] P-12. Final values: `CheckerTimeoutSeconds` and `CheckerMemorySize` are agreed with the checker author and written in the README. Starting values: 150 s (ceiling, owner approved) and 128 MB (confirmed start).

---

## Part D. Deployment checklist (every step needs the owner's approval before it runs)

Only the owner decides when to start. Each step names its gate and its check. The agent proposes the exact command and waits for approval. Account-specific values (stack names, region, email, bucket, distribution id) are given at run time and are not committed.

- [ ] D-0. **Gate:** the checker author's pull request is merged into `main`, and P-0 to P-12 have passed. If either is false, do not continue.
- [ ] D-1. Budget stack (`ops/budget.yaml`) with the email passed on the command line. Check: the confirmation email from AWS is received and accepted by the owner. (Requirement 10.)
- [ ] D-2. Demo stack with `InitialMode=ok`. **Check by calling the demo URL directly** (`curl -sS -o /dev/null -w "%{http_code}\n" <demo-url>`): it must return **200** and the body `demo target: ok`. A 403 means a resource-policy statement is missing (`InvokeFunctionUrl` or `InvokeFunction`, design 7.3), and the checker would count the demo target as `down` in every mode. Stop and fix it before D-4. Also call it once with `MODE=down` set in the console and check for 503, then set `MODE=ok` again.
- [ ] D-3. Measurement of candidate sites (design 8), from the checker's region, several times at different hours: status code and total time for each URL. Drop a candidate whose normal time is close to 2000 ms or which answers 403 or an unexpected 3xx. Record the result in the pull request (numbers, not URLs of the owner's account). Open item C-4 (IPv6) is looked at here. This step may need a cost approval if it runs from a Lambda or another AWS resource. A measurement from a laptop is only a first filter (design 8).
- [ ] D-4. **Account-id check of the demo URL** (after the direct call in D-2 returned 200). Compare the real hostname with the 12-digit account id. If the hostname contains the account id or any derived string, stop: the URL is not committed and the decision returns to the owner (design 7.1). Otherwise record "no account id found" in the pull request.
- [ ] D-5. Pull request for `services.json`: remove the four `example.com` entries, add the two measured sites and the demo URL. Review, merge, and fetch `main`. (Requirements 9, 7.2 step 2.)
- [ ] D-6. **Backend stack with the schedule off.** Deploy with `CheckerScheduleState=DISABLED`, `CheckerTimeoutSeconds=150`, `CheckerMemorySize=128`, and the default `EventRetentionDays`. Note `ApiDomain` and `CheckerFunctionName`. Check: the stack is created, no schedule is running (the schedule state is `DISABLED`), and `GET /status` through the API URL returns every service as `unknown` (this is the expected result while the checker has not run).
  - **Manual one-time invocation (separate step, own approval):** invoke the checker once with the payload `{}`. Check the log for: no error, no secret, one line per service, no timeout. Check the table (read-only view or the status API) for one record per service with `lastChecked` set, and the demo service showing the expected status for `MODE=ok`. If it fails, fix the cause with the checker author. Do not enable the schedule.
- [ ] D-7. **Enable the schedule (its own step).** Re-deploy with `CheckerScheduleState=ENABLED`. **This step must not be skipped or merged into D-6.** Checks, in order: (a) the schedule state is `ENABLED`; (b) after 2 to 3 minutes, the checker log has new runs on each minute; (c) `lastChecked` of every service moves forward by about one minute between two reads; (d) no service stays `unknown` after 3 minutes. If (b) to (d) fail, the likely cause is that the schedule is still disabled or the role cannot invoke the function. The owner is told at the end of D-6 that D-7 is still open.
- [ ] D-8. Frontend stack with `ApiDomain` from D-6, then build with `VITE_DATA_SOURCE=api npm run build`, upload with `aws s3 sync`, and invalidate `/*` (design 6.3). Checks: the CloudFront domain serves the page, a direct S3 URL is refused, `GET /status` on the CloudFront domain returns 200 and valid JSON with `Cache-Control` honored for 5 s, `/status?x=1` returns the same cached object, and the page shows live data. The distribution domain is never committed.
- [ ] D-9. Update the README sentences about the checker (task B-7) to say it is deployed, and commit that change.
- [ ] D-10. Post-deploy checks of the UNVERIFIED items that need a live stack: a 429 response and its caching (a controlled test at a low rate, not a load test), an origin 5xx and how long CloudFront remembers it, and whether the status Lambda's error response carries a cache header. Record each result in `design.md`.
- [ ] D-11. First-day measurement and tuning (see Part E, task E-1).

---

## Part E. Tuning and decisions after real data

- [ ] E-1. **Lower the Timeout after the first day.** Read the checker log (`REPORT` lines): the largest `Duration`, `Max Memory Used`, and any "Task timed out" line. Proposal: new `Timeout` = max(30 s, 2 × largest `Duration`), written in the README with the date and numbers. Raise `MemorySize` one step (256 MB) only if the memory use is above about 80% or handshakes dominate the duration (design 5.4). Each change is a `sam deploy` that needs the owner's approval. The final values are agreed with the checker author.
- [ ] E-2. **Decide the default for `CheckerTimeoutSeconds`.** After the checker author agrees on a number, the owner chooses one of:

  | Option | Effect | Risk |
  |---|---|---|
  | A. Keep **no default** (current) | Every deploy needs an explicit value | A forgotten value is noticed at once. More typing for the deployer. |
  | B. Default **150** (the initial ceiling) | `sam deploy` works without the value | The ceiling stays as a silent default if nobody lowers it after E-1. |
  | C. Default = the **tuned value** after E-1 | Short default that fits real data | Wrong if `services.json` grows. Design 5.2 requires recalculating the value whenever N changes, so a comment next to the default says so. |

  Recommendation: option A until E-1 is done, then option C with a comment that names N and the date. The change is a small edit of B-1's parameter and the README, in a pull request. It needs the checker author's agreement first.
- [ ] E-3. When the checker author answers C-1 to C-4 (the owner passes the answers), update `design.md` section 13 statuses and re-run the affected P checks (C-1: P-7, C-2: P-10, C-3: P-9, C-4: D-3).
- [ ] E-4. If the author later adds an environment variable, update the template (B-1) in the same pull request that documents it, and repeat P-2.

---

## Part F. Demo, screenshots and removal

- [ ] F-1. **Demo run** (approval: owner starts it). Follow `docs/demo.md`: confirm `healthy` for the two real sites and the demo target, then set `MODE=slow` in the Lambda console and wait for `slow` (1 to 2 min), `MODE=down` for `down` (2 to 3 min), `MODE=ok` for `recovering` and then `healthy` (1 to 2 min). Compare the observed times with the table in design 7.2. Record differences in `design.md`.
- [ ] F-2. **Layout check** with the list in `docs/demo.md`: 360, 768 and 1280 px, portrait and landscape on a phone, the five pet states, the overlay, overflow, tap targets. Record failures as issues for the frontend owner. This spec does not change `src/`.
- [ ] F-3. **Screenshots** (approval: owner approves the deployed demo as a source). Take the shots in the plan. Before each commit, check that the image has no browser address bar, no AWS console, no account id, no email address. Commit the images in a separate pull request.
- [ ] F-4. **NO SIGNAL overlay screenshot** from the local method in the README (`DEV_API_TARGET=http://127.0.0.1:9`), since a deployed stack cannot be made unreachable without a change.
- [ ] F-5. **Removal** (approval: owner), in the order of design 9.4: delete the demo stack, then open a pull request that removes its entry from `services.json` (until the next deploy the checker records failures for it, which the owner accepts or avoids by deleting the stack last); empty the frontend bucket and delete the frontend stack; delete the backend stack; keep or delete the budget stack; check Cost Explorer on the next day.

---

## Traceability

| Requirement | Tasks |
|---|---|
| 1 Checker Lambda | B-1, P-1, P-2 |
| 2 IAM | B-1, B-2, B-3, P-4 |
| 3 Schedule | B-1, D-6, D-7 |
| 4 Runtime settings | B-1, P-9, P-12, E-1, E-2 |
| 5 Private hosting | B-2, D-8 |
| 6 `/status` through CloudFront | B-2, D-8, D-10 |
| 7 Build and order | B-7, D-5, D-6, D-8 |
| 8 Demo target | B-3, D-2, D-4, F-1 |
| 9 Monitored services | B-5, D-3, D-5 |
| 10 Cost | B-4, B-8, D-1, F-5 |
| 11 No secrets | every task, F-3 |
| 12 Documentation | B-7, F-2, F-3, F-4 |
| 13 Open items | E-3, E-4 |
| 14 Protected files and CI | B-6, Part C |
| 15 Approval gates | Hard conditions, Part D |
