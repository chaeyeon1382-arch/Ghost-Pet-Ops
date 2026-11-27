"""Scheduled Lambda entry point for the configured HTTPS health checks."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from ghostpet.checker_items import build_check_result
from ghostpet.checker_writer import WriteOutcome, write_check_result
from ghostpet.config import load_services
from ghostpet.http_check import BlockedAddressError, DEFAULT_TIMEOUT_S, perform_check
from ghostpet.model import format_ts

logger = logging.getLogger("checker")

_DYNAMO_CONNECT_TIMEOUT_S = 1
_DYNAMO_READ_TIMEOUT_S = 1
# Allow a read and a write plus time for Lambda to finish.
_WRITE_BUDGET_MS = 1000 * (2 * (_DYNAMO_CONNECT_TIMEOUT_S + _DYNAMO_READ_TIMEOUT_S) + 1)
# The HTTP timeout applies to each of TCP, TLS and the response. DNS has
# a separate system resolver timeout, so reserve another 15 seconds for it.
_CHECK_BUDGET_MS = int(1000 * (3 * DEFAULT_TIMEOUT_S + 15)) + _WRITE_BUDGET_MS


_ERROR_CODE_PATTERN = re.compile(r"[A-Za-z0-9_.]{1,64}")


def _error_code(exc: BaseException) -> str | None:
    """Return the AWS ``response["Error"]["Code"]`` or None when there is none."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    code = error.get("Code") if isinstance(error, dict) else None
    if isinstance(code, str) and _ERROR_CODE_PATTERN.fullmatch(code):
        return code
    return None


def _log_failure(service_id: str | None, exc: BaseException) -> None:
    """Log only the service id, exception class and AWS error code.

    The exception message and traceback are never logged: AWS error messages
    can contain IAM role ARNs and the account ID.
    """
    prefix = f"service={service_id} " if service_id is not None else ""
    logger.error("%serror_class=%s error_code=%s", prefix, type(exc).__name__, _error_code(exc))


@lru_cache(maxsize=1)
def _get_dynamodb(table_name: str) -> tuple[Any, Any]:
    import boto3  # Lambda runtime dependency
    from botocore.config import Config

    config = Config(
        connect_timeout=_DYNAMO_CONNECT_TIMEOUT_S,
        read_timeout=_DYNAMO_READ_TIMEOUT_S,
        retries={"mode": "standard", "total_max_attempts": 1},
    )
    table = boto3.resource("dynamodb", config=config).Table(table_name)
    # The table resource registers serializers on its own client. The
    # writer already serializes transaction items for the low-level API.
    client = boto3.client("dynamodb", config=config)
    return table, client


def handler(event: dict, context: Any) -> None:
    """Run a sequential check of the configured services for any scheduler payload."""
    try:
        _run_checks(context)
    except Exception as exc:
        _log_failure(None, exc)
        # Fail the invocation, but with a fixed message so the Lambda runtime's own
        # error log cannot repeat the original message or traceback.
        raise RuntimeError("Checker invocation failed") from None


def _run_checks(context: Any) -> None:
    services = load_services()
    table_name = os.environ["TABLE_NAME"]
    retention_days = int(os.environ.get("EVENT_RETENTION_DAYS", "7"))
    if not 1 <= retention_days <= 365:
        raise ValueError("EVENT_RETENTION_DAYS must be between 1 and 365")

    # Rotate with the once-per-minute schedule, including across cold starts.
    offset = int(time.time() // 60) % len(services)
    ordered = services[offset:] + services[:offset]

    def can_attempt(delay_s: float) -> bool:
        return context.get_remaining_time_in_millis() >= _WRITE_BUDGET_MS + delay_s * 1000

    for index, service in enumerate(ordered):
        if context.get_remaining_time_in_millis() < _CHECK_BUDGET_MS:
            logger.warning("Skipped %d services because Lambda time is low", len(ordered) - index)
            break

        try:
            started = time.monotonic()
            try:
                result = perform_check(service.url)
            except BlockedAddressError:
                logger.warning("Blocked resolved address for service %s", service.id)
                result = build_check_result(
                    status_code=None,
                    response_ms=int((time.monotonic() - started) * 1000),
                    timed_out=False,
                )
            checked_at = format_ts(datetime.now(UTC))

            if not can_attempt(0):
                logger.warning("Insufficient Lambda time to save check for service %s", service.id)
                continue
            table, client = _get_dynamodb(table_name)
            outcome = write_check_result(
                table,
                service,
                result,
                checked_at,
                retention_days,
                can_attempt=can_attempt,
                transaction_client=client,
            )
            if outcome is WriteOutcome.TIME_BUDGET_EXHAUSTED:
                logger.warning("Insufficient Lambda time to save check for service %s", service.id)
        except Exception as exc:
            _log_failure(service.id, exc)
