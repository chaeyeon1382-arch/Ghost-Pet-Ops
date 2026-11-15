"""Shared data model for the state table, used by the reader and the writer.

Keys, status values, the timestamp format and item validation live only here
(see .kiro/steering/structure.md). Design reference: design.md sections 2 and 8.1.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

STATUSES: tuple[str, ...] = ("unknown", "healthy", "slow", "down", "recovering")

# Keys (design 2.1, 2.2)
SERVICES_PK = "SERVICES"
EVENTS_PK = "EVENTS"
SERVICE_SK_PREFIX = "SERVICE#"
EVENT_SK_PREFIX = "EVENT#"

SECONDS_PER_DAY = 86400
_TOKEN_NAMESPACE_PREFIX = "ghost-pet-ops:"

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

COUNTER_FIELDS: tuple[str, ...] = ("consecutiveFailures", "consecutiveSlow", "consecutiveFast")
NOTE_MAX_LENGTH = 200


class InvalidStoredData(ValueError):
    """A stored item (or an item about to be written) breaks the data contract."""

    def __init__(self, service_id: str, field: str, reason: str = "") -> None:
        self.service_id = service_id
        self.field = field
        self.reason = reason
        detail = f": {reason}" if reason else ""
        super().__init__(f"invalid {field} for service {service_id}{detail}")


# --- Timestamps (design 2.3) -------------------------------------------------


def format_ts(dt: datetime) -> str:
    """Format an aware datetime as YYYY-MM-DDTHH:MM:SSZ in UTC. Fractional seconds are dropped."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def parse_ts(value: str) -> datetime:
    """Parse the exact YYYY-MM-DDTHH:MM:SSZ format. Anything else raises ValueError."""
    if not isinstance(value, str) or not _TIMESTAMP_RE.match(value):
        raise ValueError("timestamp must match YYYY-MM-DDTHH:MM:SSZ")
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)


# --- Keys, TTL, idempotency (design 2.1, 2.2, 4.3, 4.4) ----------------------


def service_sk(service_id: str) -> str:
    """Sort key of a service record: SERVICE#<id>."""
    if not isinstance(service_id, str) or not service_id or "#" in service_id:
        raise ValueError("service id must be a non-empty string without '#'")
    return SERVICE_SK_PREFIX + service_id


def event_sk(at: str, service_id: str) -> str:
    """Sort key of an event: EVENT#<at>#<serviceId>. Also the event's idempotency key."""
    parse_ts(at)
    service_sk(service_id)  # validates the id
    return f"{EVENT_SK_PREFIX}{at}#{service_id}"


def expires_at(at: str, retention_days: int) -> int:
    """TTL value in epoch seconds: at + retention_days."""
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise ValueError("retention_days must be a positive integer")
    return int(parse_ts(at).timestamp()) + retention_days * SECONDS_PER_DAY


def idempotency_token(service_id: str, checked_at: str) -> str:
    """ClientRequestToken for TransactWriteItems (36 characters), derived from (serviceId, checkedAt)."""
    service_sk(service_id)
    parse_ts(checked_at)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_TOKEN_NAMESPACE_PREFIX}{service_id}#{checked_at}"))


# --- Validation (design 4.1 step 5, 5.1 step 4) ------------------------------


def _as_int(value: Any) -> int | None:
    """Return value as int if it is a whole number (int or DynamoDB Decimal), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        return int(value)
    return None


def _non_negative_int(item: Mapping[str, Any], field: str, service_id: str) -> int:
    number = _as_int(item.get(field))
    if number is None or number < 0:
        raise InvalidStoredData(service_id, field, "must be a non-negative integer")
    return number


def _status(item: Mapping[str, Any], field: str, service_id: str) -> str:
    value = item.get(field)
    if value not in STATUSES:
        raise InvalidStoredData(service_id, field, "not a known lowercase status")
    return value


def _timestamp(item: Mapping[str, Any], field: str, service_id: str) -> str:
    value = item.get(field)
    try:
        parse_ts(value)
    except ValueError:
        raise InvalidStoredData(service_id, field, "not YYYY-MM-DDTHH:MM:SSZ") from None
    return value


def _service_id(item: Mapping[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidStoredData("?", field, "missing service id")
    return value


def validate_service_record(item: Mapping[str, Any], *, require_counters: bool = True) -> dict[str, Any]:
    """Validate a service record and return a normalized copy (Decimal -> int).

    The writer calls this with require_counters=True before every write. The reader
    projects only public fields, so it calls it with require_counters=False.
    """
    service_id = _service_id(item, "id")
    record: dict[str, Any] = {
        "id": service_id,
        "status": _status(item, "status", service_id),
        "responseMs": _non_negative_int(item, "responseMs", service_id),
        "lastChecked": _timestamp(item, "lastChecked", service_id),
    }
    for field in COUNTER_FIELDS:
        if require_counters or field in item:
            record[field] = _non_negative_int(item, field, service_id)
    return record


def validate_event(item: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an event item and return a normalized copy (Decimal -> int)."""
    service_id = _service_id(item, "serviceId")
    from_status = _status(item, "from", service_id)
    to_status = _status(item, "to", service_id)
    if from_status == to_status:
        raise InvalidStoredData(service_id, "to", "must differ from 'from'")
    note = item.get("note")
    if not isinstance(note, str) or len(note) > NOTE_MAX_LENGTH:
        raise InvalidStoredData(service_id, "note", f"must be a string of at most {NOTE_MAX_LENGTH} characters")
    return {
        "serviceId": service_id,
        "from": from_status,
        "to": to_status,
        "at": _timestamp(item, "at", service_id),
        "note": note,
        "expiresAt": _non_negative_int(item, "expiresAt", service_id),
    }
