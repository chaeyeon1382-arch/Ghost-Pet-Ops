# Design: AWS deployment structure, frontend hosting and demo

Implements `requirements.md` in this folder. Steering documents in `.kiro/steering/` apply.
Related: `state-store-read-api/design.md` (write contract 4, CloudFront route 6.3, SAM 7) and `frontend-status-api/design.md`.

No template or code is written by this design. YAML below is illustrative and is not applied until the tasks are approved.

## 0. How to read the status marks

| Mark | Meaning |
|---|---|
| **CONFIRMED** | Stated by the checker author, or verified in the code on `main`. The source is named. |
| **PROPOSAL** | This design's suggestion. The owner or the checker author has not agreed yet. |
| **TBD (미확정)** | Not decided. Not assumed anywhere in this design. A deploy-time value or a task waits for it. |
| **UNVERIFIED** | A statement from memory or from a source that could not be opened in this session. A task checks it. |

## 1. Baseline: what exists on `main`

Checked after `git fetch` on `ade97ff` (merge of PR #4, `feature/checker`). Branch `feature/aws-deploy-demo` was fast-forwarded to it. None of these files are changed by this spec.

| File | State on `main` | Notes |
|---|---|---|
| `backend/src/ghostpet/decision.py` | exists | Python port of `petState.ts`. `is_failure`: timeout, no status code, or status >= 400. 3xx is a success. |
| `backend/src/ghostpet/checker_items.py` | exists | Pure builders for `CheckResult`, service record, event item, idempotency token. |
| `backend/src/ghostpet/http_check.py` | exists | `perform_check(url, timeout_s=5.0)`. See 1.1. |
| `backend/tests/test_decision.py`, `test_checker_items.py`, `test_http_check.py` | exist | |
| `backend/src/ghostpet/checker_writer.py` | **does not exist** | Teammate, new branch, in progress. |
| `backend/src/checker_app.py` | **does not exist** | Teammate, new branch, in progress. |
| Draft PR for writer and handler | **none yet** (`gh api` shows PRs #1 to #4, all closed) | Completion date TBD (미확정). |
| `backend/src/ghostpet/services.json` | unchanged (four `example.com` placeholders) | |
| `backend/template.yaml` | unchanged by PR #4 | Still has the role and log group, but no checker function. |

### 1.1 What `http_check.py` does (read from the code)

| Behavior | Evidence in code | Status |
|---|---|---|
| Only `https://` URLs. Other schemes raise `ValueError`. | `perform_check` start | CONFIRMED |
| DNS resolve with `socket.getaddrinfo`, then validate the **first** address: loopback, link-local (includes `169.254.169.254`), private, unspecified, multicast, reserved are refused. | `_is_blocked_address` | CONFIRMED |
| Connection goes to the validated IP. SNI and certificate check use the original host name. | `_PinnedHTTPSConnection` | CONFIRMED (DNS-rebinding defence) |
| Proxy environment variables are ignored (`ProxyHandler({})`). | `build_opener` | CONFIRMED |
| Redirects are not followed. A 3xx is returned as its own status code and counts as a success. | `_NoRedirectHandler`, `decision.is_failure` | CONFIRMED |
| Response body is not read or logged. | | CONFIRMED |
| `timeout_s` is a **per-socket-operation** limit. DNS is not covered. | module docstring | CONFIRMED (author's note) |
| On timeout: `statusCode=None`, `responseMs=5000`, `timedOut=True`. Other connection, DNS and TLS errors: `statusCode=None`, `timedOut=False`. | | CONFIRMED |
| **A blocked address raises `BlockedAddressError`** instead of returning a failed `CheckResult`. | `perform_check` | CONFIRMED in code. See open item C-1. |
| URL allow-list is **not** enforced in `perform_check`. `checker_app.py` must pass only URLs from `load_services()`. | docstring | CONFIRMED (author: enforced in `checker_app.py`, not written yet) |

## 2. Architecture

```
                     [Backend stack: backend/template.yaml]
EventBridge Scheduler ──(rate 1 min, input {})──> CheckerFunction ──GetItem/PutItem/UpdateItem──> StateTable
 (scheduler role:                                 checker_app.handler                                 ▲
  InvokeFunction on                               existing CheckerFunctionRole                        │ Query
  this function only)                             existing CheckerFunctionLogGroup                    │
                                                                      StatusApi ──> StatusFunction ───┘
                                                                         ▲   (Outputs: ApiDomain)
                     [Frontend stack: frontend/template.yaml]            │
Browser ──> CloudFront ── default behavior ──> S3 bucket (private, OAC)  │
                      └── /status (cache 5 s) ───────────────────────────┘ HTTPS-only origin

                     [Demo stack: demo/template.yaml, deleted after the demo]
Checker ──HTTPS GET──> Function URL ──> demo Lambda (MODE = ok | slow | down)
Checker ──HTTPS GET──> two large public sites

                     [Ops stack: ops/budget.yaml]   monthly cost budget with email alerts
```

Files: see section 14. Region: TBD (미확정). CI validates in `us-east-1` only for the SAM transform. Cost figures in this design assume `us-east-1` (PROPOSAL, same as `state-store-read-api/design.md` 2.5).

## 3. Interface with the checker author

### 3.1 Confirmed

| Item | Value | Source |
|---|---|---|
| Handler | `checker_app.handler` (file `backend/src/checker_app.py`, not yet written) | CONFIRMED, checker author |
| Environment variables | `TABLE_NAME`, `EVENT_RETENTION_DAYS` (default 7 days) | CONFIRMED, checker author |
| Service URLs | Read from `ghostpet/services.json` by `load_services()`, not from environment variables | CONFIRMED, checker author |
| Scheduler payload | Empty JSON object `{}` | CONFIRMED, checker author |
| 3xx | Not followed, counted as success | CONFIRMED, owner decision and `http_check.py` / `decision.py` |
| Address validation before connecting | Implemented (see 1.1) | CONFIRMED in code |
| URL allow-list enforcement | Done in `checker_app.py` | CONFIRMED as a plan. The file does not exist yet. |
| Memory start value | 128 MB, then adjust from measured duration and memory use | CONFIRMED as a starting point |

### 3.2 TBD (미확정): not assumed anywhere

| Item | What the design does meanwhile |
|---|---|
| Additional environment variables | The template sets only `TABLE_NAME` and `EVENT_RETENTION_DAYS`. Nothing else. |
| Sequential or parallel checks | Sequential is the design basis (author's instruction). Final choice TBD. |
| Lambda `Timeout` | Template parameter with **no default**. Candidates and reasoning in section 5. |
| Final `MemorySize` | Parameter, default 128 (the confirmed starting value). Final value TBD after measurement. |
| Date of the writer and handler | TBD. Draft PR will be shared first. |
| What the handler does with the `{}` payload | The author said the payload is `{}`. Whether the handler reads it is not stated. The design assumes it is ignored and lists this as a check in 12. |

## 4. Backend template changes (`backend/template.yaml`)

Existing resources are not changed, except the `Description` comment of `CheckerFunctionRole`, the log group comment ("No function uses it yet"), and the two Output descriptions that say "future checker". New resources:

### 4.1 `CheckerFunction`

```yaml
# illustrative
CheckerFunction:
  Type: AWS::Serverless::Function
  Properties:
    FunctionName: !Sub ${AWS::StackName}-checker
    CodeUri: src/
    Handler: checker_app.handler            # CONFIRMED
    Runtime: python3.12
    Architectures: [arm64]
    MemorySize: !Ref CheckerMemorySize      # default 128 (confirmed start), final TBD
    Timeout: !Ref CheckerTimeoutSeconds     # no default, TBD (section 5)
    Role: !GetAtt CheckerFunctionRole.Arn   # existing role
    LoggingConfig:
      LogGroup: !Ref CheckerFunctionLogGroup  # existing log group
    Environment:
      Variables:
        TABLE_NAME: !Ref StateTable                    # CONFIRMED
        EVENT_RETENTION_DAYS: !Ref EventRetentionDays  # CONFIRMED (parameter default is already 7)
    EventInvokeConfig:
      MaximumRetryAttempts: 0
      MaximumEventAgeInSeconds: 60
```

- No `ReservedConcurrentExecutions` (owner decision, requirement 4.4). No VPC. No layers.
- The function name matches the log group name `/aws/lambda/${AWS::StackName}-checker` for readability. `LoggingConfig.LogGroup` is what actually binds them.
- New parameters: `CheckerMemorySize` (Number, default 128, allowed 128 to 1024 in steps handled by `MinValue`/`MaxValue`), `CheckerTimeoutSeconds` (Number, **no default**, `MinValue: 10`, `MaxValue: 900`), `CheckerScheduleState` (see 4.3).
- A parameter with no default means `sam deploy` asks for a value, so nobody deploys with a number the team has not chosen. `sam validate --lint` does not need a value.
- **Why `EventInvokeConfig` with zero retries.** EventBridge Scheduler invokes a Lambda target **asynchronously** ([AWS docs](https://docs.aws.amazon.com/lambda/latest/dg/services-cloudwatchevents.html)). By default Lambda retries an asynchronous invocation twice after a function error, waiting one and then two minutes ([AWS docs](https://docs.aws.amazon.com/en_us/lambda/latest/dg/invocation-async-error-handling.html)). A retry of a one-minute check would run a stale schedule tick alongside later ones and send extra requests to the monitored sites. The next tick is one minute away, so a retry gives no benefit. Retries are set to 0 and the event age to 60 s (the minimum).

### 4.2 `CheckerSchedulerRole` and `CheckerSchedule`

An explicit `AWS::Scheduler::Schedule` and an explicit role are used instead of SAM's `ScheduleV2` event source.

| | Explicit `AWS::Scheduler::Schedule` + role (PROPOSAL) | SAM `ScheduleV2` |
|---|---|---|
| Role trust and policy | Written out and reviewable (requirement 2.2) | Generated by SAM. Property names could not be checked in this session because the SAM docs page returned 403. |
| Retry policy, payload, window | Plain CloudFormation properties | Supported, but not verified here |
| Amount of YAML | About 30 lines more | Less |

```yaml
# illustrative
CheckerSchedulerRole:
  Type: AWS::IAM::Role
  Properties:
    Description: Lets EventBridge Scheduler invoke the checker Lambda and nothing else.
    AssumeRolePolicyDocument:
      Version: "2012-10-17"
      Statement:
        - Effect: Allow
          Principal: { Service: scheduler.amazonaws.com }
          Action: sts:AssumeRole
          Condition:
            StringEquals:
              aws:SourceAccount: !Ref AWS::AccountId     # pseudo parameter, no literal id
    Policies:
      - PolicyName: invoke-checker
        PolicyDocument:
          Version: "2012-10-17"
          Statement:
            - Effect: Allow
              Action: lambda:InvokeFunction
              Resource: !GetAtt CheckerFunction.Arn      # this function only

CheckerSchedule:
  Type: AWS::Scheduler::Schedule
  Properties:
    Name: !Sub ${AWS::StackName}-checker
    ScheduleExpression: rate(1 minute)
    FlexibleTimeWindow: { Mode: "OFF" }
    State: !Ref CheckerScheduleState
    Target:
      Arn: !GetAtt CheckerFunction.Arn
      RoleArn: !GetAtt CheckerSchedulerRole.Arn
      Input: "{}"                                        # CONFIRMED payload
      RetryPolicy:
        MaximumRetryAttempts: 2
        MaximumEventAgeInSeconds: 60
```

- The role has one action on one resource. This is the only `Resource` that is not a wildcard and the template has no `Resource: "*"` (requirement 2.3).
- `aws:SourceAccount` in the trust policy limits the role to this account's Scheduler (confused-deputy protection). Adding an `aws:SourceArn` condition for the schedule is also possible (the ARN can be built from the fixed schedule name). Both conditions are UNVERIFIED against the Scheduler documentation in this session. A task checks them. If the check fails, the condition is removed and the reason is noted here.
- **Scheduler retry policy.** Scheduler's default is to retry for up to 24 hours and up to 185 times when delivery fails ([AWS re:Post](https://repost.aws/knowledge-center/eventbridge-resolve-failedinvocation-errors)). Delivered late, a one-minute tick is worthless and would pile up. The policy is limited to 2 attempts within 60 s. Delivery means "Lambda accepted the event". A function error after that is handled by 4.1, not by this policy.
- `CheckerScheduleState` (`ENABLED` or `DISABLED`). **Owner approved: default `DISABLED`.** The first deploy creates everything with the schedule off. The deployer invokes the function once by hand, reads the log, and then enables the schedule with a second `sam deploy`. This keeps "deploy" and "start spending requests on other people's sites" as two separate decisions. The owner can choose `ENABLED` as default instead.
- The schedule is a stack resource, so it is deleted with the stack (requirement 3.5).

### 4.3 Outputs

New: `CheckerFunctionName` (for a manual test invoke) and `CheckerScheduleName`. The existing `CheckerFunctionRoleArn` and `CheckerFunctionLogGroupName` outputs stay (the README tells the checker author to use them, and the function now uses them in the same template).

### 4.4 Overlapping runs are safe without a concurrency limit

`ReservedConcurrentExecutions` is not set (owner decision). A new account can have a total concurrency limit that leaves nothing to reserve, because AWS keeps a minimum of unreserved concurrency (UNVERIFIED here). More importantly it is not needed. If two invocations run at the same time (for example when `Timeout` is above 60 s, section 5), the write contract in `state-store-read-api/design.md` section 4 makes them safe:

| Situation | Protection (design section) |
|---|---|
| Both read version N and both try to write | The record write is conditional on `version = N` and `lastChecked < :checkedAt`. One wins. The other re-reads with a strongly consistent read, re-decides, and may drop its check. (4.1 step 7, 4.2, 4.5) |
| A transition is written twice | The event `Put` is conditional on `attribute_not_exists(pk)`. The record update is in the same transaction. Duplicate events cannot exist. (4.3) |
| A retry after a network timeout | `ClientRequestToken` from `(serviceId, checkedAt)`. (4.4) |
| Both create the first record | `attribute_not_exists(pk)` lets one succeed. (4.2) |
| A very late invocation with an older timestamp | The `lastChecked` condition and the stale check (4.1 step 3) drop it. |

Costs of overlap that the contract does not remove: the monitored sites receive up to as many requests per minute as there are overlapping runs (at most about 3 with the longest timeout in section 5), and each run costs Lambda time. Both are small. Lambda async retries are off (4.1), so overlap only comes from a slow run, not from retries.

This protection holds **only if the writer follows the contract**. `checker_writer.py` is not written yet, so pre-deploy task P-3 (section 12) checks it against section 4 of the read-API design before any deployment.

## 5. Lambda `Timeout`: analysis and candidates (TBD, 미확정)

### 5.1 Why "4 services x 5 s = 20 s" is wrong

The checker author states, and `http_check.py` documents, that the 5 s HTTP limit applies to each socket operation, not to the whole request, and that it does not apply to DNS resolution. A check can therefore take longer than 5 s, and the code has no whole-request limit. The author's estimate of one service's worst case:

| Phase | Upper bound | Source |
|---|---|---|
| DNS (`getaddrinfo`, OS resolver) | about 15 s | `http_check.py` docstring. Author's estimate, **not measured**. |
| TCP connect | 5 s | `timeout_s` |
| TLS handshake | 5 s | `timeout_s` |
| Wait for response headers | 5 s | `timeout_s` |
| One service | **about 30 s** | docstring ("~30 s max") |

This "30 s" is not a guarantee. Each `recv` has its own 5 s limit, so a server that sends header bytes slowly could keep a request open longer. The Lambda `Timeout` is the only hard limit.

DynamoDB time per service (PROPOSAL, not measured): about 2 s allowance. Normal calls take milliseconds. The write contract allows up to 3 attempts, each with a `GetItem` and a write, plus backoff of 100 ms and 300 ms. A stuck AWS endpoint is not bounded by this allowance. boto3's default connect and read timeouts are long (60 s each, UNVERIFIED), so open item C-2 asks the checker author to set shorter client timeouts.

### 5.2 Formula

```
T_run  =  T_cold_start  +  N x ( T_check_worst  +  T_ddb )

Normal day (estimate, not measured):   1 s + 3 x (0.3 to 1.0 s + 0.05 s)            ≈ 2 to 5 s
Author's worst case, N = 4:            1 s + 4 x (30 s + 2 s)                       ≈ 129 s
```

`N` is the number of services. The plan has 3 (two real sites, one demo target, section 9). Sizing uses **N = 4** to leave room for one more. **Every change of `services.json` that raises N requires recalculating the Timeout.** `services.json` allows up to 25 entries, which would give a worst case of about 800 s, and that exceeds what this design covers.

### 5.3 Candidates

| `Timeout` | Covers | When it is too small | Overlap with the next tick (1 min) |
|---|---|---|---|
| 30 s | Normal days. One service at its worst. | A single hanging service ends the run. All services after it in the list are skipped, and the same thing happens every minute. | No |
| 60 s | Normal days. One hanging service plus the others (about 32 s + 3 x 1 s). | Two hanging services. | No |
| 120 s | Three services at their worst plus normal ones. | All four at worst (129 s). | Yes, at most 2 runs |
| **150 s (PROPOSAL)** | The author's worst case for N = 4 with about 15% margin. | More than 4 services, or checks that exceed the author's estimate. | Yes, at most 3 runs |
| 180 s or more | Larger N. | | Yes |

**What a timeout does:** Lambda stops the process without cleanup. Services that were already written stay written (each service is written on its own). The service in progress loses its check. Services after it get no check in that run. With a fixed order and a persistent hang in an early service, later services could be starved every minute. That is the strongest argument for a larger timeout, and it can also be solved in the checker's code (open item C-3), not in the template.

**Cost of a long timeout:** it costs nothing on normal days, because billing follows actual duration. Worst-case ceiling: every run lasts the full timeout, 43,200 runs per month x 150 s x 0.125 GB = 810,000 GB-s. Lambda's free tier is 400,000 GB-s per month and arm64 is priced per GB-s (about 10 USD at the ceiling, UNVERIFIED, price page checked in the cost-verification task). This ceiling also needs every site to hang every minute, so it is not an expected cost. The budget alert (section 9) covers it.

**Owner approved 150 s as the initial ceiling** (the final value still needs the checker author's agreement). Deploy with 150 s, then lower it after the first day of real data. Suggested rule, also a proposal: new `Timeout` = max(30 s, 2 x the largest `Duration` in the CloudWatch log), recalculated whenever N changes. The deployer must still type the number, because the parameter has no default. **The final value stays TBD until the checker author agrees.**

### 5.4 `MemorySize`

Start at 128 MB (CONFIRMED starting point). The checks wait for the network, so CPU is rarely the limit, though a TLS handshake takes more CPU at 128 MB. Adjustment rule (PROPOSAL): read the `REPORT` log line. If `Max Memory Used` is above about 80% of the setting, or `Duration` is dominated by handshakes, raise it one step (256 MB) and compare. The final value is TBD. The parameter `CheckerMemorySize` allows changing it without editing the template.

### 5.5 Log retention

The existing `LogRetentionDays` parameter (default 14) already applies to `CheckerFunctionLogGroup`. No change.

## 6. Frontend stack (`frontend/template.yaml`)

Plain CloudFormation (no Lambda, so no SAM transform is needed). Parameters: `ApiDomain` (String, pattern `^[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com$`, no default), `PriceClass` (default `PriceClass_100`, PROPOSAL, because viewers are few and this lowers cost).

| Resource | Design |
|---|---|
| `FrontendBucket` | All four public-access blocks on. `BucketEncryption` SSE-S3. `OwnershipControls: BucketOwnerEnforced`. No website hosting. No versioning (the site is rebuilt from git). No access logging (requirement 5.7). Name is generated, not hard-coded. |
| `FrontendOac` | `AWS::CloudFront::OriginAccessControl`, type S3, signing `always`, protocol `sigv4`. |
| `FrontendBucketPolicy` | One statement: principal `cloudfront.amazonaws.com`, action `s3:GetObject` on the bucket's objects only, condition `AWS:SourceArn` equal to this distribution's ARN (built with `!Sub` and the pseudo parameters `AWS::AccountId`, no literal id). |
| `StatusCachePolicy` | `AWS::CloudFront::CachePolicy`. `MinTTL: 0`, `DefaultTTL: 5`, `MaxTTL: 10`. Headers, cookies and query strings: none (so `/status?x=1` hits the same cache entry and a viewer cannot bypass the cache). Gzip and Brotli on. |
| `FrontendDistribution` | See 6.1. |
| Outputs | `DistributionDomainName` and `BucketName`. Both are deployment values and are never copied into a committed file. |

### 6.1 Distribution

- **Origins:** the bucket's regional domain with `FrontendOac`, and the API (`ApiDomain`) as a custom origin with `OriginProtocolPolicy: https-only`, TLS 1.2 only, and `OriginReadTimeout: 10` (the status Lambda has a 5 s timeout and cache `MaxTTL` is 10 s).
- **Default behavior** (static files): S3 origin. Methods GET and HEAD. Redirect HTTP to HTTPS. Compression on. Managed cache policy `CachingOptimized` and managed response headers policy `SecurityHeadersPolicy` (IDs are re-checked against the AWS docs at implementation time, UNVERIFIED here).
- **`/status` behavior:** path pattern `/status` (exact path, query strings are ignored by the pattern). API origin. Methods GET and HEAD only. `StatusCachePolicy`. Origin request policy `AllViewerExceptHostHeader`, because API Gateway needs its own `Host` (from the read-API design 6.3). Redirect HTTP to HTTPS. Same security-headers policy.
- **Default root object:** `index.html`. No custom error response that replaces a status with `index.html` (requirement 5.5). Reason: custom error responses apply to the whole distribution, so a 403 from `/status` would become an HTML page with status 200. The frontend would then fail on JSON parsing instead of seeing the real HTTP failure, which is still handled, but would be misleading.
- **Error caching:** `CustomErrorResponses` entries for 500, 502, 503 and 504 with **only** `ErrorCachingMinTTL: 1` (no `ResponseCode` and no `ResponsePagePath`). They keep the original status and body and shorten how long CloudFront remembers the error. Without this, CloudFront keeps an origin error for its default (10 s, UNVERIFIED), so a short backend problem would outlive the problem. A 429 from API Gateway is not in the list of codes that can be configured, and whether CloudFront caches it is UNVERIFIED. It is limited in any case by the 10 s cache `MaxTTL`. Task F-5 checks this on the deployed stack.
- **TLS:** default CloudFront certificate and domain (no custom domain). With the default certificate CloudFront fixes the minimum protocol version, and the `MinimumProtocolVersion` setting cannot raise it (UNVERIFIED, documented behavior as remembered). This limit is accepted and is stated in the README instead of being hidden.
- **IPv6** on. **HTTP version** `http2` (default). **Price class** from the parameter. No WAF, no logging, no Origin Shield, no custom domain.
- **The direct API URL stays reachable.** CloudFront needs it as an origin, and HTTP API has no way to accept only CloudFront without extra code. Stage throttling (`ThrottleRateLimit` 20, `ThrottleBurstLimit` 50) still protects it. A secret origin header is not used (it would need an authorizer Lambda, which adds cost and code for a demo).

### 6.2 Same-origin result

The browser asks `/status` on the CloudFront domain. No CORS is needed. `AllowedOrigin` stays empty in the backend stack, so the API sends no CORS headers (requirement 6.4).

### 6.3 Deploy and upload (documented with placeholders only)

```bash
VITE_DATA_SOURCE=api npm run build           # relative /status, no host in the bundle
aws s3 sync dist/ s3://<bucket>/ --delete
aws cloudfront create-invalidation --distribution-id <distribution-id> --paths "/*"
```

- `--paths "/*"` is used instead of `/index.html` (the existing README text). Whether an invalidation of `/index.html` also clears the cached copy served for `/` through the default root object is UNVERIFIED. A wildcard counts as one path. The README is updated to match.
- The build needs no stack output (requirement 7.3). The order is in section 11.
- The `aws` commands are documentation only. They are not run without approval (requirement 15).

## 7. Demo target (`demo/template.yaml`, `demo/src/app.py`)

A small stack, deleted after the demo.

| Part | Design |
|---|---|
| `DemoFunction` | `python3.12`, `arm64`, 128 MB, `Timeout: 10`. Reads `MODE` from the environment. Parameter `InitialMode` (default `ok`, allowed `ok`, `slow`, `down`) sets it at deploy time. |
| `DemoFunctionRole` | Only `logs:CreateLogStream` and `logs:PutLogEvents` on its own log group (requirement 2.4). No access to the state table. |
| `DemoLogGroup` | Retention 7 days. |
| Function URL | `AuthType: NONE`. This is the only public endpoint of the demo. SAM's `FunctionUrlConfig` also creates the permission that allows public calls. |

Behavior of the handler (about 20 lines, written only after the tasks are approved):

| `MODE` | Response | Pet reaction (checker rules) |
|---|---|---|
| `ok` | 200, short fixed text, immediately | fast check |
| `slow` | wait **3.0 s** (fixed in code), then 200 | slow check (`responseMs` above 2000, below the 5 s limit) |
| `down` | 503, short fixed text | failure |
| anything else | same as `ok` | |

The delay is a constant in the code and is not read from the request, so a caller cannot make the function wait longer (requirement 8.3). The body is a fixed string, and the handler logs only the mode, never headers or bodies (requirement 8.5).

**Changing the state:** edit `MODE` in the Lambda console (Configuration, Environment variables). The next invocation uses it. Console edits make the stack drift from the template. That is acceptable for a stack deleted after the demo, and the README says so.

**Abuse and cost.** A Function URL has no throttling of its own. Anyone who finds the URL can call it. At `slow`, one request keeps the function busy for 3 s, which is 0.375 GB-s at 128 MB. One million such requests would cost a few USD (order of magnitude, price UNVERIFIED). Mitigations: the stack is short-lived, the URL is not advertised, the delay is fixed, and the budget alert exists. `ReservedConcurrentExecutions` is not used on this function either, for the same reason as in 4.4. Residual risk is stated in the README.

### 7.1 Does a Function URL hostname contain the AWS account ID? (requirement 8.7)

- The documented format is `https://<url-id>.lambda-url.<region>.on.aws`. The page says the endpoint is **unique** and **never changes** after creation ([Lambda docs](https://docs.aws.amazon.com/lambda/latest/dg/lambda-urls.html); format shown in [the configuration page](https://docs.aws.amazon.com/ja_jp/lambda/latest/dg/urls-configuration.html)).
- The format has no account-ID segment. The pages that could be read do not say how `<url-id>` is generated. The detailed page (`urls-invocation.html`) could not be opened in this session (HTTP 403). **So the statement "the host does not contain the account ID" is UNVERIFIED.**
- Decision for the owner (decision 1 is "commit the demo URL"). Because the documentation does not settle it, the task list contains a check **before** the URL is committed: after the demo stack exists (this needs the owner's deployment approval), look at the real hostname. If it contains the 12-digit account ID, or any string that equals it, the URL is not committed and the decision returns to the owner. If it does not, the URL is committed with a note in the pull request. This check cannot prove how the id is generated. It shows only that the real value has no account ID in it, which is what matters for the repository.
- The region in the hostname is public information and is accepted.

### 7.2 Timeline after a `MODE` change (from `decision.py`)

Thresholds: 2 slow checks make `slow` (`SLOW_CHECKS_NEEDED`), 3 failures make `down` (`FAILURES_TO_DOWN`), the first success after `down` makes `recovering`, 2 fast checks make `healthy` (`FAST_CHECKS_NEEDED`). The schedule is every 60 s, so the first check after the change comes between 0 and 60 s later, and each further check 60 s after that. The page adds up to 15 s (10 s polling interval plus 5 s edge cache).

| Change | Checks needed | Time until the service row changes |
|---|---|---|
| (first run) `unknown` to `healthy` | 1 fast check | up to 1 min |
| `healthy` to `slow` (`MODE=slow`) | 2 slow checks | 1 to 2 min |
| `healthy` or `slow` to `down` (`MODE=down`) | 3 failures | 2 to 3 min |
| `down` to `recovering` (`MODE=ok`) | 1 success | up to 1 min |
| `recovering` to `healthy` | 1 more fast check (the first success already counted as fast 1) | 1 to 2 min after `MODE=ok` in total |
| `slow` to `healthy` (`MODE=ok`) | 2 fast checks | 1 to 2 min |

A demo run of about 8 minutes: `ok` (healthy) then `slow` (2 min) then `down` (3 min) then `ok` (2 min to healthy). The pet shows the **worst** status of all services, and `unknown` ranks worse than `healthy` (`frontend-status-api` design 10.1). The two real sites must be `healthy` for the demo target to drive the pet.

The earlier chat estimate of "3 minutes" for recovery was too long. The code needs the first success plus one more fast check, which is 2 checks, not 3.

## 8. Monitored services (`services.json`)

- **Plan:** two large public sites and one demo target entry (N = 3). One demo entry is enough, because both entries would follow the same `MODE`. (Owner can ask for two.)
- The four `example.com` placeholders are removed. `https://example.com/docs`, `/api/health` and `/status` very likely answer 404, which counts as a failure, so the pet would show `down`.
- **Candidates (PROPOSAL, nothing measured):** `https://www.wikipedia.org/`, `https://www.python.org/`. Spare: `https://github.com/`. The sandbox cannot reach the Internet (every request returned connection code 000), so no response time was measured here.
- **Selection rules:** answers the exact URL with 2xx or 3xx without relying on redirect-following, normal response time clearly below 2000 ms (`SLOW_THRESHOLD_MS`), stable, and light for one request per minute. Neither site is run by the team, so the monitoring is one `GET` per minute with no extra headers.
- **Measurement (before the URLs are committed):** from the checker's own AWS region, after the owner approves a deployment or a short test, run each URL several times at different hours and record status code and total time. A measurement from another network is not final. A candidate whose normal time is close to 2 s is dropped.
- **Risks to check in that measurement:** a site may answer cloud IP ranges with 403 (it would show as `down`). `perform_check` validates and uses only the **first** address from `getaddrinfo`. If the first address were IPv6 and the Lambda has no IPv6 route, the connection would fail and be counted as a failure (UNVERIFIED, open item C-4).
- **Commit rule:** `services.json` and the PR contain no account ID, key or email. The demo URL follows 7.1.

## 9. Cost and budget

### 9.1 Budget (`ops/budget.yaml`)

**PROPOSAL: a separate small stack**, not part of the backend stack. The owner chose "include Budgets in the template" but did not say which one. Reasons for a separate file: the notification email is a required parameter, which would otherwise be needed on every backend deploy and delete. The budget is account-level, so deleting the backend stack should not delete it. The owner approved the separate file. CI validation of it is not added yet (the owner limited the CI change to `frontend/` and `demo/`).

- `AWS::Budgets::Budget`: cost budget, monthly, limit parameter `BudgetLimitUsd` (default 10).
- Notifications: actual 50%, actual 100%, forecasted 100%, to the subscriber from the parameter `NotificationEmail`.
- `NotificationEmail`: String, **no default**, `AllowedPattern` for an address. It is passed at deploy time and never written in a committed file.
- Region: the Budgets API is served from `us-east-1`, so deploying this stack in `us-east-1` is PROPOSED (UNVERIFIED). The other stacks may use another region (TBD).
- **Limits (requirement 10.6):** a budget covers the whole account and not one stack. Cost data arrives with a delay of several hours, so a burst of cost is seen late. It sends email only and does not stop anything.
- Price of Budgets: UNVERIFIED here (a cost-verification task checks the pricing page and records the date).

### 9.2 Monthly estimate (volumes are calculated, prices are checked in a cost-verification task)

| Service | Monthly volume | Note |
|---|---|---|
| Lambda checker | 43,200 invocations. About 2 to 5 s each (estimate) at 128 MB, so roughly 11,000 to 27,000 GB-s. | Inside the free tier (400,000 GB-s, 1M requests, UNVERIFIED). |
| EventBridge Scheduler | 43,200 invocations | Inside the free allowance (UNVERIFIED). |
| DynamoDB | 3 services x 43,200 = 129,600 checks. About 130k reads and 130k writes, plus reader requests. | Already estimated at about 0.13 USD for 4 services in `state-store-read-api/design.md` 2.5. |
| API Gateway and status Lambda | Reader requests that pass the 5 s edge cache | One origin request per 5 s per edge location at most, much lower than viewer polls. |
| CloudFront | Demo traffic only | Inside the free tier (UNVERIFIED). |
| S3 | A few hundred KB stored | Negligible. |
| CloudWatch Logs | A few log lines per run | Small. Retention 14 days. |
| Demo Lambda | 43,200 checks plus any outside callers | Free-tier range. Abuse case in section 7. |
| Budgets | 1 budget | UNVERIFIED. |

Worst-case ceilings: the status API 73 USD (existing analysis, 6.2 of the read-API design), the checker about 10 USD (5.3), the demo target depends on outside callers. The budget limit of 10 USD would alert before these are reached.

### 9.3 Fixed-cost items not used

WAF, NAT Gateway, custom domain, Provisioned Concurrency, VPC endpoints, provisioned DynamoDB capacity, Origin Shield, CloudFront access logs (requirement 10.1).

### 9.4 Removal after the demo (requirement 10.7)

1. Delete the demo stack. Then remove its entry from `services.json` in a pull request, or the checker would record failures for it.
2. Empty the frontend bucket, then delete the frontend stack.
3. Delete the backend stack (the table is not retained, `DeletionPolicy: Delete`).
4. Keep or delete the budget stack. It costs nothing to keep.
5. Check that no CloudWatch log group remains, and look at the next day's Cost Explorer.

## 10. Documentation, demo checks and screenshots (requirement 12)

- **README (`README.md`, `backend/README.md`):** the architecture, the order in section 11, parameters with the "no default" ones marked, the cost summary, the budget step, the removal steps in 9.4, and the note on TLS in 6.1. The sentences "The checker ... does not exist yet" are replaced only after the checker is deployed.
- **Demo script:** the steps and timings from 7.2, with the expected pet state for each step.
- **Layout check list (desktop and mobile):** widths 360, 768 and 1280 px, portrait and landscape on a phone; pet states `unknown`, `healthy`, `slow`, `down`, `recovering`; the NO SIGNAL overlay (dimmed data, "stale since" text); long service names do not overflow; event rows wrap; tap targets are usable; text contrast with the dimmed state; no horizontal scroll.
- **Screenshots (`docs/screenshots/`):** named `<number>-<state>-<viewport>.png`, for example `01-unknown-desktop.png`, `02-healthy-mobile.png`. Taken only from a deployment the owner approved. The browser address bar and any AWS console area are cropped or not captured (requirement 11.3). The NO SIGNAL overlay is captured locally with the documented `DEV_API_TARGET=http://127.0.0.1:9` method, since a deployed stack cannot be made unreachable without a change.
- **Commands** use placeholders (`<bucket>`, `<distribution-id>`, `<region>`).

## 11. Deployment order and gates (nothing is deployed by this spec)

| Step | Action | Gate |
|---|---|---|
| 0 | Teammate's writer and `checker_app.py` PR is **merged into `main`** | **No deployment of the backend or the checker before this.** (requirement from the owner) |
| 1 | Pre-deploy reconciliation (section 12) | All checks pass. Differences are written down and settled with the checker author. |
| 2 | Budget stack | Owner approval. |
| 3 | Demo stack (`MODE=ok`) | Owner approval. Then the hostname check in 7.1. |
| 4 | Pull request: `services.json` with the real sites and the demo URL | Reviewed and merged. Measurement from 8 is done first. |
| 5 | Backend stack with `CheckerScheduleState=DISABLED`, a chosen `CheckerTimeoutSeconds`. Note `ApiDomain`. | Owner approval. |
| 6 | Invoke the checker once by hand with `{}`. Read the log and the table. | The invocation succeeds, one record per service, no secret in the log. |
| 7 | Enable the schedule (`CheckerScheduleState=ENABLED`) | Owner approval. |
| 8 | Frontend stack with `ApiDomain`. | Owner approval. |
| 9 | Build with `VITE_DATA_SOURCE=api`, upload, invalidate. | |
| 10 | Demo run and screenshots | |

Demo URL before backend: the checker reads `services.json` from the deployed code, so the demo URL must exist before step 5, as the owner decided.

## 12. Pre-deploy reconciliation (becomes tasks)

Before any deployment, compare the checker code on `main` with the template.

| # | Check | Pass condition |
|---|---|---|
| P-0 | The writer and handler PR is merged and `ade97ff` or later contains `checker_app.py` and `checker_writer.py` | Both files exist on `main` |
| P-1 | Handler | `checker_app.py` defines `handler(event, context)`, and the template's `Handler` equals `checker_app.handler` |
| P-2 | Environment variables | Every `os.environ` / `os.getenv` in `checker_app.py`, `checker_writer.py` and the files they import is in the template, and nothing in the template is unused. Today the template has `TABLE_NAME` and `EVENT_RETENTION_DAYS` only. Any additional variable is a TBD (미확정) that blocks the deploy until it is added with its meaning. |
| P-3 | Write contract | `checker_writer.py` follows `state-store-read-api/design.md` section 4 (conditional writes, conditional event `Put`, `ClientRequestToken`, retry rules). Needed for 4.4. |
| P-4 | IAM actions | Every DynamoDB call used is covered by `GetItem`, `PutItem`, `UpdateItem`. For example a `Query`, `Scan` or `ConditionCheck` would need a role change. |
| P-5 | Payload | The handler works with `{}` and does not depend on the event contents. |
| P-6 | URL allow-list | `checker_app.py` passes only URLs from `load_services()` to `perform_check`. |
| P-7 | Blocked address | `BlockedAddressError` is caught per service, logged without secrets, and recorded as a failed check. Otherwise one bad host would end the whole run (open item C-1). |
| P-8 | Logging | No secret, header or body in logs. |
| P-9 | Order and N | The services order, sequential or parallel, and N are known, and the Timeout formula in 5.2 is recalculated with them. |
| P-10 | boto3 client timeouts | The DynamoDB client has short connect and read timeouts (open item C-2). |
| P-11 | `sam validate --lint` | Passes for all templates, and `sam build` succeeds. |
| P-12 | Final values | `CheckerTimeoutSeconds` and `CheckerMemorySize` are agreed with the checker author and written in the README. |

## 13. Items to confirm with the checker author (requirement 13)

| # | Item | Status |
|---|---|---|
| 1 | Handler name | **CONFIRMED**: `checker_app.handler`. Checked again in P-1. |
| 2 | Environment variables | **CONFIRMED**: `TABLE_NAME`, `EVENT_RETENTION_DAYS` (default 7). **TBD (미확정)**: any additional variable. |
| 3 | Sequential or parallel | **TBD (미확정)**. Sequential is the design basis. |
| 4 | Timeout | **TBD (미확정)**. Candidates and reasons in 5.3. Proposal: 150 s as a ceiling, then lower. |
| 5 | Memory size | **128 MB CONFIRMED as the start**. Final value **TBD (미확정)**. |
| 6 | Payload | **CONFIRMED**: `{}`. Handler behavior with it: TBD (checked in P-5). |
| 7 | 3xx | **CONFIRMED**: not followed, counted as success (code: `http_check.py`, `decision.py`). |
| 8 | Schedule retry behavior the checker expects | **TBD (미확정)**. Proposal: no Lambda retries and a Scheduler retry limited to 60 s (4.1, 4.2). |
| C-1 | `perform_check` raises `BlockedAddressError` instead of returning a failed result. Does `checker_app.py` catch it per service? | **TBD (미확정)** |
| C-2 | DynamoDB client timeouts and retries (boto3 defaults are long) | **TBD (미확정)** |
| C-3 | A hanging first service starves later services each minute (fixed order). Rotation, a wall-clock guard or parallel checks? | **TBD (미확정)** |
| C-4 | The first `getaddrinfo` result is the only one used. A first IPv6 result could fail on a Lambda without IPv6 routes. | **TBD (미확정)**. Checked in the measurement in section 8. |
| C-5 | Draft PR timing for `checker_writer.py` and `checker_app.py` | **TBD (미확정)** |

No file of the checker author is changed to resolve these. Questions go to the author as comments or a short note.

## 14. Files (requirement 14)

| File | Change |
|---|---|
| `.kiro/specs/aws-deploy-demo/{requirements,design,tasks}.md` | new |
| `backend/template.yaml` | add parameters (3), `CheckerFunction`, `CheckerSchedulerRole`, `CheckerSchedule`, 2 outputs. Update comments that say "future checker". |
| `frontend/template.yaml` | new (section 6) |
| `demo/template.yaml`, `demo/src/app.py` | new (section 7) |
| `ops/budget.yaml` | new (9.1). Approved by the owner. |
| `backend/src/ghostpet/services.json` | real sites and demo URL (section 8) |
| `README.md`, `backend/README.md` | section 10 |
| `docs/screenshots/` | screenshot plan and, later, images |
| `.github/workflows/ci.yml` | add `sam validate --lint` steps for `frontend/` and `demo/` templates (approved by the owner, steps only, same permissions and no credentials). `ops/budget.yaml` is not validated by CI until the owner approves it |

**Not touched:** `decision.py`, `checker_items.py`, `http_check.py`, `checker_writer.py`, `checker_app.py`, their tests, `src/` (frontend), `petState.ts`.

### 14.1 CI

The existing `backend` job validates with `sam validate --lint --template template.yaml --region us-east-1`. The new steps use the same SAM CLI version and the same region, with `--template ../frontend/template.yaml` and so on. Templates with required parameters that have no default are expected to validate. If lint rejects them, the fix is a harmless placeholder default in the parameter's own `Default` only where the value is not secret, which is decided when it happens. A passing validation does not prove that a stack deploys (requirement 14.5).

## 15. Changes needed in `requirements.md`

The answers from the checker author make some requirement texts outdated. They are listed here and **not edited until approved**:

| Requirement | Current text | Needed change |
|---|---|---|
| 1.3 | Handler from a parameter with no default | Handler is `checker_app.handler`, written in the template. |
| 1.4, 1.5 | Environment variables TBD | `TABLE_NAME` and `EVENT_RETENTION_DAYS` are confirmed. Others stay TBD. |
| 3.4 | Payload TBD | Payload `{}` is confirmed. |
| 4.1 | Timeout TBD and formula | Keep TBD. Say the formula must not use "N x 5 s". |
| 8.8 | Timeline table | Recovery takes 2 checks, not 3. |
| 9.3 | The checker author confirms 3xx | Confirmed and seen in the code. |
| 13 | Items 1, 2, 6, 7 | Statuses updated as in section 13. New items C-1 to C-5. |
| 2.2, 3.3 | Retry policy to be chosen | Chosen in 4.1 and 4.2 (PROPOSAL). |
| 14.1 | The five files do not exist | Three exist on `main`. Still not edited. |
