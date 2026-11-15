# Ghost Pet Ops backend

DynamoDB state store and the read-only `GET /status` API, defined with AWS SAM.
Spec: [`.kiro/specs/state-store-read-api/`](../.kiro/specs/state-store-read-api/). Response format: [`.kiro/steering/api-format.md`](../.kiro/steering/api-format.md).

```
template.yaml          table, HTTP API, status Lambda, reader and writer roles
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
3. **Set up a cost alert.** Create an AWS Budgets monthly cost budget, for example $10, with email alerts at 50% and 100% of actual spend and 100% of forecast spend. It is account-level, so it is not part of this template (design 6.2).

## Deploy order (recommended: same CloudFront distribution)

Nothing is deployed by this repository's CI. When you deploy manually:

1. `sam build && sam deploy --guided` for this stack, leaving `AllowedOrigin` empty, which keeps CORS off. Note the `ApiDomain` output.
2. Deploy the frontend stack (S3 + CloudFront) with `ApiDomain` as the origin for a `/status` behavior. Use origin request policy `AllViewerExceptHostHeader` and a cache TTL of 0, 5 or 10 s.
3. Build the frontend with a relative `/status` URL, upload it, and invalidate `/index.html`.

Details and the CORS alternative: design section 6.3. `samconfig.toml` is git-ignored. Keep account-specific settings out of the repo.

## Parameters

| Parameter | Default | Notes |
|---|---|---|
| `ThrottleRateLimit` | 20 | Requests per second for the whole API. Lower it to cut the worst-case cost (design 6.2). |
| `ThrottleBurstLimit` | 50 | Token bucket size |
| `AllowedOrigin` | empty | Empty means no CORS. Set one origin only if not using the CloudFront route. `*` is rejected. |
| `EventRetentionDays` | 7 | Event TTL, for the checker |
| `LogRetentionDays` | 14 | CloudWatch Logs retention |

## For the checker author

The checker Lambda is not built yet. Follow the write contract in **design section 4**:
- Use conditional writes on `version` and `lastChecked`.
- Write a transition and its event together with `TransactWriteItems`, using a `ClientRequestToken` from `idempotency_token()`.
- The event `Put` must be conditional on `attribute_not_exists(pk)`. Retries re-read the record and re-run the decision logic.

Import these, and don't redefine them (design 8.1):
- From `ghostpet.model`: keys, statuses, `format_ts`, `expires_at`, `idempotency_token`, and the `validate_*` functions.
- From `ghostpet.config`: `load_services()`. This is the only source of URLs to check.

Attach the function to the `CheckerFunctionRoleArn` output (`GetItem`, `PutItem` and `UpdateItem` on this table only), and use the `CheckerFunctionLogGroupName` output as its `LoggingConfig.LogGroup`.

The config validator only checks the URL text. A public name can still resolve to a private address. Before connecting, the checker must resolve the host and refuse private, loopback, link-local and metadata addresses. It must also not follow redirects.
