"""GET /status Lambda handler (design.md section 5).

I/O only: load config, run two Queries, shape with ghostpet.status_response, return JSON.
Response format: .kiro/steering/api-format.md.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ghostpet import config, model
from ghostpet.status_response import MAX_EVENTS, build_status_response

logger = logging.getLogger("status")
logger.setLevel(logging.INFO)

ERROR_BODY = {"error": {"code": "internal_error", "message": "Status is temporarily unavailable."}}
SUCCESS_CACHE_CONTROL = "public, max-age=5"

# Loaded once per cold start. An error is kept, not raised, so every request can
# return the agreed error body instead of API Gateway's generic 500.
try:
    _SERVICES: list[config.Service] | None = config.load_services()
    _CONFIG_ERROR: str | None = None
except config.ConfigError as exc:
    _SERVICES, _CONFIG_ERROR = None, str(exc)

_table: Any = None


def _get_table() -> Any:
    """Create the boto3 Table lazily, so tests and imports never touch AWS."""
    global _table
    if _table is None:
        import boto3  # provided by the Lambda runtime

        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def handler(event: dict, context: Any) -> dict:
    return handle(services=_SERVICES, config_error=_CONFIG_ERROR, table_factory=_get_table)


def handle(
    *,
    services: list[config.Service] | None,
    config_error: str | None,
    table_factory: Callable[[], Any],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    started = time.monotonic()

    if config_error is not None or not services:
        _log(logging.ERROR, "config_invalid", started, reason=config_error or "no services")
        return _error_response()

    try:
        table = table_factory()
        records = query_service_records(table)
        events = query_recent_events(table)
    except Exception as exc:  # noqa: BLE001 - boto3 ClientError, connection errors, missing TABLE_NAME
        _log(logging.ERROR, "dynamodb_error", started, errorCode=_error_code(exc))
        return _error_response()

    try:
        body = build_status_response(services, records, events, now())
    except model.InvalidStoredData as exc:
        _log(logging.ERROR, "invalid_stored_data", started,
             serviceId=exc.service_id, field=exc.field, reason=exc.reason)
        return _error_response()
    except Exception as exc:  # noqa: BLE001 - any bug must still return the agreed error body
        _log(logging.ERROR, "internal_error", started, errorType=type(exc).__name__)
        return _error_response()

    _log(logging.INFO, "status_ok", started, services=len(body["services"]), events=len(body["events"]))
    return _response(200, body, SUCCESS_CACHE_CONTROL)


def query_service_records(table: Any) -> list[dict]:
    """All service records: one partition, following pagination. Counters are not projected."""
    kwargs = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
        "ExpressionAttributeValues": {":pk": model.SERVICES_PK, ":prefix": model.SERVICE_SK_PREFIX},
        "ProjectionExpression": "#id, #status, responseMs, lastChecked",
        "ExpressionAttributeNames": {"#id": "id", "#status": "status"},
        "ConsistentRead": False,
    }
    items: list[dict] = []
    while True:
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def query_recent_events(table: Any) -> list[dict]:
    """The newest MAX_EVENTS events: one Query, newest first, one page."""
    page = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :prefix)",
        ExpressionAttributeValues={":pk": model.EVENTS_PK, ":prefix": model.EVENT_SK_PREFIX},
        ProjectionExpression="serviceId, #from, #to, #at, note, expiresAt",
        ExpressionAttributeNames={"#from": "from", "#to": "to", "#at": "at"},
        ScanIndexForward=False,
        Limit=MAX_EVENTS,
        ConsistentRead=False,
    )
    return page.get("Items", [])


def _response(status_code: int, body: dict, cache_control: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Cache-Control": cache_control},
        "body": json.dumps(body, separators=(",", ":")),
    }


def _error_response() -> dict:
    return _response(500, ERROR_BODY, "no-store")


def _error_code(exc: Exception) -> str:
    """The AWS error code if present (for example ProvisionedThroughputExceededException), else the type name."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str) and code:
            return code
    return type(exc).__name__


def _log(level: int, event: str, started: float, **fields: Any) -> None:
    """One JSON line per request. Never logs request headers, response bodies, or secrets."""
    line = {"event": event, "durationMs": round((time.monotonic() - started) * 1000), **fields}
    logger.log(level, json.dumps(line, default=str))
