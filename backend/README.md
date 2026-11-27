# Ghost Pet Ops backend

DynamoDB state store, the read-only `GET /status` API and the scheduled checker, defined with AWS SAM.
Spec: [`.kiro/specs/state-store-read-api/`](../.kiro/specs/state-store-read-api/). Response format: [`.kiro/steering/api-format.md`](../.kiro/steering/api-format.md).

```
template.yaml          table, HTTP API, status Lambda, checker Lambda, 1-minute schedule, reader, writer and scheduler roles
src/                   Lambda code root (CodeUri for every function)
  status_app.py        GET /status handler
  ghostpet/            shared package: model, config, services.json, response shaping
tests/                 pytest, no AWS access needed
```

## Run the tests

Python 3.12, the same version as the Lambda runtime.

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest
```

CI runs the same tests, plus `sam validate --lint`, in the `backend` job of `.github/workflows/ci.yml`.
Validation runs offline and uses only a region, never AWS credentials.

## Before the first deploy

1. **Replace the placeholder URLs.** `src/ghostpet/services.json` contains `https://example.com/...` placeholders only. Replace them with the reviewed real sites in a pull request before deploying.
   - The validator rejects:
     - non-https URLs and URLs with user info,
     - IP-address hosts, `localhost`, and single-label or internal names (`.local`, `.internal`, `.localdomain`, `.arpa`),
     - more than 25 entries.
   - Don't put secrets or tokens in the URLs. The file is public in the repo.
2. **Validate locally:** `sam validate --lint --template template.yaml --region <your-region>`.
3. **Set up a cost alert** with `ops/budget.yaml`: a monthly cost budget (default 10 USD) with email alerts at 50% and 100% of actual spend and at 100% of forecast spend. It covers the whole account, so it is a separate stack, and the email address is a required parameter with no default. Pass it when you deploy, never write it in a file:
   ```bash
   sam deploy --template-file ../ops/budget.yaml --stack-name <budget-stack> --region us-east-1 \
     --parameter-overrides NotificationEmail=<your-email> --capabilities CAPABILITY_IAM
   ```
   AWS Budgets data arrives a few hours late, and the budget only sends email. Budgets without actions are free ([AWS Budgets FAQ](https://aws.amazon.com/aws-cost-management/aws-budgets/faqs/)).

## Deploy order (same CloudFront distribution)

Nothing is deployed by this repository's CI. Spec: [`.kiro/specs/aws-deploy-demo/`](../.kiro/specs/aws-deploy-demo/).

**Do not deploy this stack before both of these are true:**
1. The checker author's pull request (`checker_writer.py` and `checker_app.py`) is merged into `main`.
2. The pre-deploy checks P-0 to P-12 (`.kiro/specs/aws-deploy-demo/tasks.md`, part C) have passed. They compare the checker code with this template: handler name, environment variables, DynamoDB actions, payload, timeout.

Each step below needs the owner's approval, because it creates AWS resources that can cost money.

1. Budget stack (`ops/budget.yaml`), see above.
2. Demo stack (`demo/`), then check the demo URL (see the demo section of [`docs/demo.md`](../docs/demo.md)). Commit the URL to `services.json` in a pull request only after checking that it contains no AWS account id.
3. This stack, with the schedule **off**. Note `ApiDomain` and `CheckerFunctionName`:
   ```bash
   sam build && sam deploy --guided --parameter-overrides CheckerScheduleState=DISABLED CheckerTimeoutSeconds=<seconds>
   ```
   Leave `AllowedOrigin` empty, which keeps CORS off.
4. **Invoke the checker once by hand** with the payload `{}`, read its log and check the table. Every service must have a record.
5. **Turn the schedule on (a separate step).** Deploy again with `CheckerScheduleState=ENABLED`. If you skip this step the checker never runs, every service stays `unknown`, and the dashboard shows the waiting pet forever. After 2 to 3 minutes the checker log must show a run every minute and `lastChecked` must move.
6. Frontend stack (S3 + CloudFront) with `ApiDomain`, then build, upload and invalidate (see [`README.md`](../README.md), "AWS deployment").

Details and the CORS alternative: design section 6.3. `samconfig.toml` is git-ignored. Keep account-specific settings out of the repo.

## Parameters

| Parameter | Default | Notes |
|---|---|---|
| `ThrottleRateLimit` | 20 | Requests per second for the whole API. Lower it to cut the worst-case cost (design 6.2). |
| `ThrottleBurstLimit` | 50 | Token bucket size |
| `AllowedOrigin` | empty | Empty means no CORS. Set one origin only if not using the CloudFront route. `*` is rejected. |
| `EventRetentionDays` | 7 | Event TTL, for the checker |
| `LogRetentionDays` | 14 | CloudWatch Logs retention (all Lambda log groups of this stack) |
| `CheckerMemorySize` | 128 | Checker memory in MB. 128 is the starting value agreed with the checker author. The final value is TBD (미확정) until the first measurements. |
| `CheckerTimeoutSeconds` | none | Checker timeout in seconds. **No default on purpose, and TBD (미확정)** until agreed with the checker author. The owner approved 150 as the initial ceiling, to be lowered after the first day of measurements. The 5 s HTTP limit is per socket operation and does not cover DNS, so do not compute it as "services x 5 s". Recalculate when the number of services changes (design 5). |
| `CheckerScheduleState` | `DISABLED` | `ENABLED` or `DISABLED`. See the deploy order above. |

## For the checker author

`template.yaml` already defines `CheckerFunction` (handler `checker_app.handler`, environment `TABLE_NAME` and `EVENT_RETENTION_DAYS`) and its schedule. The code (`checker_app.py`, `checker_writer.py`) is still being written, so nothing is deployed. The template sets no other environment variable, so tell the infrastructure owner before you read a new one. The schedule sends the payload `{}`. Follow the write contract in **design section 4**:
- Use conditional writes on `version` and `lastChecked`.
- Write a transition and its event together with `TransactWriteItems`, using a `ClientRequestToken` from `idempotency_token()`.
- The event `Put` must be conditional on `attribute_not_exists(pk)`. Retries re-read the record and re-run the decision logic.

Import these, and don't redefine them (design 8.1):
- From `ghostpet.model`: keys, statuses, `format_ts`, `expires_at`, `idempotency_token`, and the `validate_*` functions.
- From `ghostpet.config`: `load_services()`. This is the only source of URLs to check.

The function already uses `CheckerFunctionRole` (`GetItem`, `PutItem` and `UpdateItem` on this table only) and `CheckerFunctionLogGroup`. A DynamoDB action outside these three needs a role change, so ask first. Asynchronous retries are off (`MaximumRetryAttempts: 0`), because the next scheduled check is one minute away.

The config validator only checks the URL text. A public name can still resolve to a private address. Before connecting, the checker must resolve the host and refuse private, loopback, link-local and metadata addresses. It must also not follow redirects.
