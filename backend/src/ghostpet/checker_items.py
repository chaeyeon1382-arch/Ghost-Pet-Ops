"""Pure helper functions for the checker Lambda.

Builds CheckResult dicts and DynamoDB item payloads from raw check data.
No I/O, no clock, no boto3 — everything here is deterministic given the same inputs.

Design references: .kiro/specs/state-store-read-api/design.md sections 2, 4.1–4.4.
Shared identifiers are always imported from ghostpet.model (design 8.1).
"""

from __future__ import annotations

from typing import Any

from ghostpet.decision import CheckResult, Transition
from ghostpet.model import (
    EVENTS_PK,
    SERVICES_PK,
    event_sk,
    expires_at,
    idempotency_token,
    service_sk,
    validate_event,
    validate_service_record,
)


# ---------------------------------------------------------------------------
# 1. build_check_result
# ---------------------------------------------------------------------------


def build_check_result(
    *,
    status_code: int | None,
    response_ms: int,
    timed_out: bool,
) -> CheckResult:
    """Normalise raw HTTP check data into a CheckResult dict.

    Rules (design 4.1 step 1):
    - ``status_code`` is None when the connection failed or timed out at the
      TCP/TLS level (no HTTP response at all).
    - ``response_ms`` must be a non-negative integer.  On timeout it is
      conventionally set to the timeout ceiling (5 000 ms), but any non-negative
      value is accepted — the caller supplies it from the actual elapsed time.
    - ``timed_out`` is True only when the check exceeded the timeout; a connection
      error that is not a timeout must set ``timed_out=False`` and
      ``status_code=None``.

    Raises ValueError on invalid arguments so callers can catch bad data before
    any downstream logic runs.
    """
    if not isinstance(timed_out, bool):
        raise ValueError("timed_out must be a bool")
    if not isinstance(response_ms, int) or isinstance(response_ms, bool) or response_ms < 0:
        raise ValueError("response_ms must be a non-negative integer")
    if status_code is not None and (
        isinstance(status_code, bool) or not isinstance(status_code, int) or status_code < 100
    ):
        raise ValueError("status_code must be None or an integer >= 100")
    if timed_out and status_code is not None:
        raise ValueError("status_code must be None when timed_out is True")

    return CheckResult(statusCode=status_code, responseMs=response_ms, timedOut=timed_out)


# ---------------------------------------------------------------------------
# 2. build_service_record
# ---------------------------------------------------------------------------


def build_service_record(
    *,
    service_id: str,
    service_name: str,
    status: str,
    response_ms: int,
    checked_at: str,
    consecutive_failures: int,
    consecutive_slow: int,
    consecutive_fast: int,
    expected_version: int | None,
) -> dict[str, Any]:
    """Build the DynamoDB item dict for a service record (design 2.1).

    This function constructs the **full item** (all writable fields).  The caller
    uses it to build the UpdateItem expression values.

    ``expected_version`` drives the version counter:
    - None  →  first write, version becomes 1   (ConditionExpression: attribute_not_exists(pk))
    - int   →  subsequent write, version becomes expected_version + 1

    The item is validated with ``validate_service_record`` before being returned,
    so an invalid status or negative counter raises ``InvalidStoredData`` here
    rather than silently reaching DynamoDB (design 4.1 step 5).

    Returns a plain dict suitable for use as expression attribute values in
    ``UpdateItem``; it does **not** include ``pk``/``sk`` (those are the Key).
    The caller must supply them separately.
    """
    next_version = 1 if expected_version is None else expected_version + 1

    # Build a full item so validate_service_record can check every field.
    candidate: dict[str, Any] = {
        "id": service_id,
        "name": service_name,
        "status": status,
        "responseMs": response_ms,
        "lastChecked": checked_at,
        "consecutiveFailures": consecutive_failures,
        "consecutiveSlow": consecutive_slow,
        "consecutiveFast": consecutive_fast,
        "version": next_version,
    }

    # validate_service_record checks id, status, responseMs, lastChecked, and all
    # three counters (require_counters=True is the default).  It raises
    # InvalidStoredData on the first bad field, so the caller can log and abort.
    validate_service_record(candidate)

    return {
        # DynamoDB table key (design 2.1)
        "pk": SERVICES_PK,
        "sk": service_sk(service_id),
        # Public fields
        "id": service_id,
        "name": service_name,
        "status": status,
        "responseMs": response_ms,
        "lastChecked": checked_at,
        # Internal counters (never returned by the read API)
        "consecutiveFailures": consecutive_failures,
        "consecutiveSlow": consecutive_slow,
        "consecutiveFast": consecutive_fast,
        # Optimistic-lock version counter (design 4.2)
        "version": next_version,
    }


# ---------------------------------------------------------------------------
# 3. build_event_item
# ---------------------------------------------------------------------------


def build_event_item(
    *,
    service_id: str,
    transition: Transition,
    checked_at: str,
    retention_days: int,
) -> dict[str, Any]:
    """Build the DynamoDB item dict for a status-transition event (design 2.2).

    Key design notes:
    - ``pk`` is always EVENTS_PK ("EVENTS").
    - ``sk`` is ``EVENT#<checked_at>#<service_id>`` — also the idempotency key
      (design 4.4).
    - ``"from"`` is the Python-reserved word.  ``transition["from_status"]`` (the
      field name used inside decision.py) is mapped to the DynamoDB key ``"from"``
      here (the only place this translation happens — design 8.1 note).
    - ``expiresAt`` is epoch seconds: ``epoch(checked_at) + retention_days * 86400``
      (design 2.2).

    The item is validated with ``validate_event`` before being returned (design
    4.1 step 5), which catches mismatched from/to statuses, bad timestamps, an
    overly long note, etc.

    Raises ``InvalidStoredData`` on invalid data so the caller can log and abort.
    """
    item: dict[str, Any] = {
        "pk": EVENTS_PK,
        "sk": event_sk(checked_at, service_id),
        # Map from_status (Python-safe name in decision.py) → "from" (DynamoDB contract)
        "serviceId": service_id,
        "from": transition["from_status"],
        "to": transition["to"],
        "at": checked_at,
        "note": transition["note"],
        "expiresAt": expires_at(checked_at, retention_days),
    }

    # validate_event checks serviceId, from/to statuses (and that they differ),
    # the timestamp format, note length, and expiresAt.
    validate_event(item)

    return item


# ---------------------------------------------------------------------------
# 4. idempotency_token helper (re-exported for checker_app.py convenience)
# ---------------------------------------------------------------------------


def build_idempotency_token(service_id: str, checked_at: str) -> str:
    """Return the ClientRequestToken for TransactWriteItems (design 4.3, 4.4).

    Thin re-export of ``model.idempotency_token`` so checker_app.py has a single
    import point for all checker item helpers.
    """
    return idempotency_token(service_id, checked_at)


__all__ = [
    "build_check_result",
    "build_service_record",
    "build_event_item",
    "build_idempotency_token",
]
