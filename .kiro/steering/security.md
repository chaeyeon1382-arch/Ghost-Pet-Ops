---
inclusion: always
---
# Security Rules

## No secrets in the repo
- Never commit credentials, API keys, `.env` files, or deployment config containing secrets or account IDs.
- `.gitignore` excludes `.env*`, `*.pem`, `.aws-sam/`, `samconfig.toml`.
- Runtime configuration goes in SAM parameters or SSM Parameter Store. Use Secrets Manager only if a real secret is needed.
- Do not log secrets, full request headers, or response bodies from monitored services.

## Least-privilege IAM
- One IAM role per Lambda, scoped to the exact resources it touches.
  - `checker`: `dynamodb:GetItem`, `PutItem`, `UpdateItem` on the state table only.
  - `status`: `dynamodb:GetItem`, `Query` (read only) on the state table only.
- Prefer SAM policy templates (`DynamoDBReadPolicy`, `DynamoDBCrudPolicy`) scoped by `TableName`. No `*` resources, no `AdministratorAccess`.
- The EventBridge Scheduler role may only invoke the `checker` function.

## Networking and cost
- No NAT Gateway. Lambdas run outside a VPC and reach the internet directly. DynamoDB does not need a VPC.
- Monitored URLs are a fixed allowlist in configuration. The checker must not fetch arbitrary user-supplied URLs (SSRF risk).
- Health checks: HTTPS GET, 5 second timeout, redirects not followed.

## API
- API Gateway: enable throttling. Restrict CORS to the frontend origin, not `*`, once that origin is known.
- `GET /status` is read only and exposes no internal details.
