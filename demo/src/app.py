"""Demo target for Ghost Pet Ops. Spec: .kiro/specs/aws-deploy-demo/design.md section 7.

A tiny HTTP endpoint (Lambda Function URL) whose behavior is chosen by the MODE environment variable,
which the presenter edits in the Lambda console:

  ok    200 immediately       -> the checker sees a fast, successful check
  slow  200 after 3.0 s       -> slow (over 2000 ms) but below the checker's 5 s limit
  down  503 immediately       -> a failed check

Any other value behaves like "ok". Nothing from the request is used, so a caller cannot change the delay.
Only the mode is logged, never headers or bodies.
"""

import json
import os
import time

# Fixed on purpose. Above SLOW_THRESHOLD_MS (2000) and below the checker's 5 s timeout.
SLOW_DELAY_S = 3.0

MODES = ("ok", "slow", "down")


def _response(status_code: int, body: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
        "body": body,
    }


def handler(event, context):
    mode = os.environ.get("MODE", "ok")
    if mode not in MODES:
        mode = "ok"
    print(json.dumps({"mode": mode}))

    if mode == "down":
        return _response(503, "demo target: down")
    if mode == "slow":
        time.sleep(SLOW_DELAY_S)
        return _response(200, "demo target: slow")
    return _response(200, "demo target: ok")
