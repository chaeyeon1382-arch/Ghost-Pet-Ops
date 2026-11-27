"""DynamoDB writer for the checker Lambda.

Implements the write contract from design.md section 4:
  - GetItem (ConsistentRead=True) to read the previous state.
  - Stale-check: drop if lastChecked >= checkedAt.
  - decide() to compute the next state.
  - Validation before every write (design 4.1 step 5).
  - Conditional UpdateItem when there is no transition (design 4.2).
  - TransactWriteItems when there is a transition (design 4.3).
  - Optimistic-lock retry up to MAX_ATTEMPTS total (design 4.1 step 7).
  - Idempotency: event sk + ClientRequestToken (design 4.4).

Design references: .kiro/specs/state-store-read-api/design.md sections 4.1–4.5.
"""

from __future__ import annotations

import enum
import logging
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

from ghostpet.checker_items import (
    build_event_item,
    build_idempotency_token,
    build_service_record,
)
from ghostpet.config import Service
from ghostpet.decision import (
    INITIAL_TRACKER,
    CheckResult,
    Decision,
    Tracker,
    decide,
)
from ghostpet.model import (
    EVENTS_PK,
    SERVICES_PK,
    InvalidStoredData,
    service_sk,
)

logger = logging.getLogger("checker_writer")

# Maximum attempts including the first (design 4.1 step 7: "at most 3 attempts in total").
MAX_ATTEMPTS: int = 3

# DynamoDB error codes used to classify TransactionCanceledException reasons.
_CCF = "ConditionalCheckFailed"
_TRANSACTION_CONFLICT = "TransactionConflict"
_THROTTLING = "ThrottlingError"


class WriteOutcome(enum.Enum):
    """Result of one write_check_result() call."""

    WRITTEN = "written"
    """Record (and optionally event) written successfully."""

    STALE = "stale"
    """checkedAt <= lastChecked: a newer result is already stored; this check is dropped."""

    ALREADY_APPLIED = "already_applied"
    """Event sk already exists: an earlier attempt already wrote this check; treated as success."""

    RETRIES_EXHAUSTED = "retries_exhausted"
    """Version conflict persisted across all MAX_ATTEMPTS attempts; check is dropped."""

    VALIDATION_ERROR = "validation_error"
    """Pre-write validation failed (bad status, counter, timestamp, or note)."""

    DYNAMO_ERROR = "dynamo_error"
    """Unclassified DynamoDB error; check is dropped."""

    TIME_BUDGET_EXHAUSTED = "time_budget_exhausted"
    """The invocation had insufficient time for another DynamoDB operation."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_check_result(
    table: Any,
    service: Service,
    check_result: CheckResult,
    checked_at: str,
    retention_days: int,
    *,
    can_attempt: Callable[[float], bool] | None = None,
    transaction_client: Any = None,
) -> WriteOutcome:
    """Persist one health-check result for *service* following design.md section 4.

    Parameters
    ----------
    table:
        A boto3 DynamoDB Table resource (or any object with compatible
        ``get_item`` and ``update_item`` / ``meta.client.transact_write_items``
        methods).  Injectable for unit testing.
    service:
        The service configuration entry (id, name, url).
    check_result:
        The raw result from perform_check().  Only statusCode, responseMs,
        timedOut are used here; url is not re-fetched.
    checked_at:
        UTC timestamp string (YYYY-MM-DDTHH:MM:SSZ) of when the check ran.
    retention_days:
        Event TTL in days (from SAM parameter EventRetentionDays).
    can_attempt:
        Optional deadline check, with the planned backoff in seconds.
    transaction_client:
        Low-level DynamoDB client for the already-serialized transaction.

    Returns
    -------
    WriteOutcome
        One of the enum values describing what happened.
    """
    attempts = iter(range(1, MAX_ATTEMPTS + 1))
    for attempt in attempts:
        if can_attempt is not None and not can_attempt(0):
            return WriteOutcome.TIME_BUDGET_EXHAUSTED
        outcome = _attempt(
            table=table,
            service=service,
            check_result=check_result,
            checked_at=checked_at,
            retention_days=retention_days,
            attempt=attempt,
            attempts=attempts,
            can_attempt=can_attempt,
            transaction_client=transaction_client,
        )
        if outcome is not None:
            return outcome
        # outcome is None → version conflict → loop for next attempt

    # All attempts exhausted
    logger.warning(
        '{"event":"retries_exhausted","serviceId":"%s","maxAttempts":%d}',
        service.id, MAX_ATTEMPTS,
    )
    return WriteOutcome.RETRIES_EXHAUSTED


# ---------------------------------------------------------------------------
# Single attempt (steps 2–6 of design 4.1)
# ---------------------------------------------------------------------------


def _attempt(
    table: Any,
    service: Service,
    check_result: CheckResult,
    checked_at: str,
    retention_days: int,
    attempt: int,
    attempts: Iterator[int],
    can_attempt: Callable[[float], bool] | None,
    transaction_client: Any,
) -> WriteOutcome | None:
    """Run one attempt.

    Returns a WriteOutcome when the attempt concludes (success or terminal failure).
    Returns None when a version conflict requires the caller to retry.
    """
    # Step 2: Read (ConsistentRead=True).
    item, expected_version = _read_record(table, service.id)

    # Step 3: Stale check.
    if _is_stale(item, checked_at):
        logger.debug(
            '{"event":"stale_check","serviceId":"%s","checkedAt":"%s"}',
            service.id, checked_at,
        )
        return WriteOutcome.STALE

    # Step 3b: Extract previous tracker.
    prev_tracker = _extract_tracker(item)

    # Step 4: Decide.
    decision: Decision = decide(prev_tracker, check_result)
    next_tracker: Tracker = decision["tracker"]
    transition = decision.get("transition")

    # Step 5: Validate before writing.
    try:
        record = build_service_record(
            service_id=service.id,
            service_name=service.name,
            status=next_tracker["status"],
            response_ms=check_result["responseMs"],
            checked_at=checked_at,
            consecutive_failures=next_tracker["consecutiveFailures"],
            consecutive_slow=next_tracker["consecutiveSlow"],
            consecutive_fast=next_tracker["consecutiveFast"],
            expected_version=expected_version,
        )
        event_item = None
        if transition is not None:
            event_item = build_event_item(
                service_id=service.id,
                transition=transition,
                checked_at=checked_at,
                retention_days=retention_days,
            )
    except InvalidStoredData as exc:
        logger.error(
            '{"event":"validation_error","serviceId":"%s","field":"%s","reason":"%s"}',
            exc.service_id, exc.field, exc.reason,
        )
        return WriteOutcome.VALIDATION_ERROR

    # Step 6: Write.
    if can_attempt is not None and not can_attempt(0):
        return WriteOutcome.TIME_BUDGET_EXHAUSTED
    if event_item is None:
        # No transition → conditional UpdateItem (design 4.2).
        conflict = _write_no_transition(table, record, expected_version, checked_at)
    else:
        # Transition → TransactWriteItems (design 4.3).
        outcome = _write_with_transition(
            table=table,
            record=record,
            event_item=event_item,
            expected_version=expected_version,
            checked_at=checked_at,
            service_id=service.id,
            attempt=attempt,
            attempts=attempts,
            can_attempt=can_attempt,
            transaction_client=transaction_client,
        )
        if outcome == _NEED_RETRY:
            return None   # signal retry
        return outcome

    if conflict:
        return None   # version conflict → signal retry

    return WriteOutcome.WRITTEN


# ---------------------------------------------------------------------------
# DynamoDB read helper (design 4.1 step 2)
# ---------------------------------------------------------------------------


def _read_record(table: Any, service_id: str) -> tuple[dict | None, int | None]:
    """GetItem with ConsistentRead=True.

    Returns (item_or_None, expected_version_or_None).
    """
    response = table.get_item(
        Key={"pk": SERVICES_PK, "sk": service_sk(service_id)},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if item is None:
        return None, None
    version = item.get("version")
    if isinstance(version, Decimal):
        version = int(version)
    return item, int(version) if version is not None else None


# ---------------------------------------------------------------------------
# Helpers (design 4.1 steps 3, 3b)
# ---------------------------------------------------------------------------


def _is_stale(item: dict | None, checked_at: str) -> bool:
    """Return True when the stored lastChecked is >= checkedAt (design 4.1 step 3).

    The comparison is a plain string comparison, which is correct because the
    YYYY-MM-DDTHH:MM:SSZ format is lexicographically ordered (design 2.3).
    """
    if item is None:
        return False
    last_checked = item.get("lastChecked")
    if not isinstance(last_checked, str):
        return False
    return last_checked >= checked_at


def _extract_tracker(item: dict | None) -> Tracker:
    """Convert a stored service record to a Tracker, defaulting to INITIAL_TRACKER.

    DynamoDB returns numbers as Decimal; this converts them to int.
    """
    if item is None:
        return {**INITIAL_TRACKER}

    def _int(value: Any, default: int = 0) -> int:
        if isinstance(value, Decimal):
            return int(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return default

    return Tracker(
        status=item.get("status", "unknown"),
        consecutiveFailures=_int(item.get("consecutiveFailures")),
        consecutiveSlow=_int(item.get("consecutiveSlow")),
        consecutiveFast=_int(item.get("consecutiveFast")),
    )


# ---------------------------------------------------------------------------
# UpdateItem (no transition, design 4.2)
# ---------------------------------------------------------------------------

# Expression shared by UpdateItem and the Update element of TransactWriteItems.
_UPDATE_EXPRESSION = (
    "SET #id = :id, #name = :name, #status = :status, "
    "responseMs = :rms, lastChecked = :checkedAt, "
    "consecutiveFailures = :cf, consecutiveSlow = :cs, consecutiveFast = :cfa, "
    "#version = :nextVersion"
)
_EXPR_ATTR_NAMES = {
    "#id": "id",
    "#name": "name",
    "#status": "status",
    "#version": "version",
}


def _update_expr_values(record: dict, expected_version: int | None) -> dict:
    """Build ExpressionAttributeValues for UpdateItem / TransactWriteItems Update."""
    next_version = record["version"]  # already computed by build_service_record
    values: dict = {
        ":id":          record["id"],
        ":name":        record["name"],
        ":status":      record["status"],
        ":rms":         record["responseMs"],
        ":checkedAt":   record["lastChecked"],
        ":cf":          record["consecutiveFailures"],
        ":cs":          record["consecutiveSlow"],
        ":cfa":         record["consecutiveFast"],
        ":nextVersion": next_version,
    }
    if expected_version is not None:
        values[":expectedVersion"] = expected_version
    return values


def _condition_expression(expected_version: int | None) -> str:
    """ConditionExpression for first write vs. update (design 4.2)."""
    if expected_version is None:
        return "attribute_not_exists(pk)"
    return "#version = :expectedVersion AND lastChecked < :checkedAt"


def _write_no_transition(
    table: Any,
    record: dict,
    expected_version: int | None,
    checked_at: str,
) -> bool:
    """Conditional UpdateItem.  Returns True on ConditionalCheckFailedException (retry needed)."""
    kwargs: dict = {
        "Key": {"pk": record["pk"], "sk": record["sk"]},
        "UpdateExpression": _UPDATE_EXPRESSION,
        "ConditionExpression": _condition_expression(expected_version),
        "ExpressionAttributeNames": _EXPR_ATTR_NAMES,
        "ExpressionAttributeValues": _update_expr_values(record, expected_version),
    }
    try:
        table.update_item(**kwargs)
        return False   # success
    except Exception as exc:  # noqa: BLE001
        if _get_error_code(exc) == "ConditionalCheckFailedException":
            return True   # version conflict
        raise


# ---------------------------------------------------------------------------
# TransactWriteItems (with transition, design 4.3)
# ---------------------------------------------------------------------------

_NEED_RETRY = object()   # sentinel: signals "retry" to _attempt
_TRANSACTION_BACKOFF_S = (0.1, 0.3)
_TRANSIENT_TRANSACTION_ERRORS = {
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "TimeoutError",
    "TransactionConflictException",
    "ThrottlingException",
    "ProvisionedThroughputExceededException",
}


def _write_with_transition(
    table: Any,
    record: dict,
    event_item: dict,
    expected_version: int | None,
    checked_at: str,
    service_id: str,
    attempt: int,
    attempts: Iterator[int],
    can_attempt: Callable[[float], bool] | None,
    transaction_client: Any,
) -> WriteOutcome | object:
    """TransactWriteItems: record Update + event Put (design 4.3).

    Returns:
      WriteOutcome.WRITTEN           – success
      WriteOutcome.ALREADY_APPLIED   – event sk already existed (idempotent)
      WriteOutcome.DYNAMO_ERROR      – unclassified error
      _NEED_RETRY                    – version conflict; caller should retry
    """
    token = build_idempotency_token(service_id, checked_at)

    update_item: dict = {
        "Update": {
            "TableName": table.name,
            "Key": {"pk": {"S": record["pk"]}, "sk": {"S": record["sk"]}},
            "UpdateExpression": _UPDATE_EXPRESSION,
            "ConditionExpression": _condition_expression(expected_version),
            "ExpressionAttributeNames": _EXPR_ATTR_NAMES,
            "ExpressionAttributeValues": _dynamo_typed(_update_expr_values(record, expected_version)),
        }
    }

    put_item: dict = {
        "Put": {
            "TableName": table.name,
            "Item": _dynamo_typed(event_item),
            "ConditionExpression": "attribute_not_exists(pk)",
        }
    }

    client = transaction_client if transaction_client is not None else table.meta.client
    transact_items = [update_item, put_item]
    backoffs = iter(_TRANSACTION_BACKOFF_S)
    while True:
        try:
            client.transact_write_items(
                TransactItems=transact_items,
                ClientRequestToken=token,
            )
            return WriteOutcome.WRITTEN
        except Exception as exc:  # noqa: BLE001
            if _get_error_code(exc) not in _TRANSIENT_TRANSACTION_ERRORS:
                outcome = _handle_txn_cancel(exc, service_id, attempt)
                if outcome is not _NEED_RETRY or _get_cancellation_reasons(exc)[:1] == [_CCF]:
                    return outcome

        next_attempt = next(attempts, None)
        if next_attempt is None:
            return _NEED_RETRY
        delay = next(backoffs)
        if can_attempt is not None and not can_attempt(delay):
            return WriteOutcome.TIME_BUDGET_EXHAUSTED
        time.sleep(delay)
        if can_attempt is not None and not can_attempt(0):
            return WriteOutcome.TIME_BUDGET_EXHAUSTED
        attempt = next_attempt


def _handle_txn_cancel(exc: Exception, service_id: str, attempt: int) -> WriteOutcome | object:
    """Classify a TransactionCanceledException per design 4.3 rules.

    Design 4.3 specifies the following priority order:
      1. Item 2 (event) is ConditionalCheckFailed → ALREADY_APPLIED
      2. Item 1 (record) is ConditionalCheckFailed, item 2 is not → retry
      3. TransactionConflict or ThrottlingError → retry (same token)
      4. Anything else → DYNAMO_ERROR
    """
    error_code = _get_error_code(exc)

    if error_code != "TransactionCanceledException":
        logger.error(
            '{"event":"dynamo_error","serviceId":"%s","errorCode":"%s"}',
            service_id, error_code,
        )
        return WriteOutcome.DYNAMO_ERROR

    reasons = _get_cancellation_reasons(exc)
    item1_reason = reasons[0] if len(reasons) > 0 else ""
    item2_reason = reasons[1] if len(reasons) > 1 else ""

    # Rule 1: event already exists (applies regardless of item 1 reason).
    if item2_reason == _CCF:
        logger.info(
            '{"event":"already_applied","serviceId":"%s","attempt":%d}',
            service_id, attempt,
        )
        return WriteOutcome.ALREADY_APPLIED

    # Rule 2: record version conflict only.
    if item1_reason == _CCF:
        return _NEED_RETRY

    # Rule 3: transient conflict or throttle.
    if item1_reason in (_TRANSACTION_CONFLICT, _THROTTLING) or \
       item2_reason in (_TRANSACTION_CONFLICT, _THROTTLING):
        return _NEED_RETRY

    # Rule 4: unclassified.
    logger.error(
        '{"event":"dynamo_error","serviceId":"%s","reasons":"%s"}',
        service_id, reasons,
    )
    return WriteOutcome.DYNAMO_ERROR


# ---------------------------------------------------------------------------
# boto3 exception helpers
# ---------------------------------------------------------------------------


def _client_error(table: Any, code: str):
    """Return the boto3 ClientError class for use in except clauses.

    ``except _client_error(table, "ConditionalCheckFailedException"):``

    This avoids importing boto3 at module level (Lambda runtime provides it,
    but unit tests do not need it).
    """
    try:
        import botocore.exceptions  # type: ignore[import]
        return botocore.exceptions.ClientError
    except ImportError:
        return _NeverRaises   # tests inject fake exceptions directly


class _NeverRaises(Exception):
    """Placeholder used when botocore is unavailable (unit tests)."""


def _get_error_code(exc: Exception) -> str:
    """Extract the DynamoDB error code string from a boto3 ClientError."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def _get_cancellation_reasons(exc: Exception) -> list[str]:
    """Extract per-item cancellation reason codes from a TransactionCanceledException.

    Returns a list of code strings (e.g. ["ConditionalCheckFailed", "None"]).
    An absent or unrecognised structure returns [].
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return []
    raw = response.get("CancellationReasons", [])
    return [r.get("Code", "") for r in raw]


# ---------------------------------------------------------------------------
# DynamoDB wire-format helper
# ---------------------------------------------------------------------------

def _dynamo_typed(item: dict) -> dict:
    """Convert a plain Python dict to DynamoDB AttributeValue format.

    Only handles the types used in this module: str (S), int (N), Decimal (N).
    Used when calling the low-level client (transact_write_items), which
    requires typed values, as opposed to the resource API which serialises
    automatically.
    """
    result: dict = {}
    for key, value in item.items():
        if isinstance(value, str):
            result[key] = {"S": value}
        elif isinstance(value, bool):
            result[key] = {"BOOL": value}
        elif isinstance(value, (int, Decimal)) and not isinstance(value, bool):
            result[key] = {"N": str(value)}
        else:
            raise TypeError(f"_dynamo_typed: unsupported type {type(value)} for key {key!r}")
    return result


__all__ = [
    "MAX_ATTEMPTS",
    "WriteOutcome",
    "write_check_result",
    # Exposed for testing
    "_is_stale",
    "_extract_tracker",
    "_dynamo_typed",
    "_handle_txn_cancel",
    "_get_cancellation_reasons",
]
