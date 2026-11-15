"""Builds the GET /status response body. Reader only. Pure: no boto3, no clock, no environment.

Format: .kiro/steering/api-format.md. Design reference: design.md section 5.1 step 4.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from ghostpet.model import InvalidStoredData, validate_event, validate_service_record

MAX_EVENTS = 20

# Allowlists: only these keys ever reach the response, so counters and internal
# attributes (pk, sk, version, expiresAt) cannot leak.
SERVICE_FIELDS: tuple[str, ...] = ("id", "name", "status", "responseMs", "lastChecked")
EVENT_FIELDS: tuple[str, ...] = ("serviceId", "from", "to", "at", "note")


class ConfiguredService(Protocol):
    id: str
    name: str


def build_status_response(
    services: Sequence[ConfiguredService],
    records: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Shape stored items into the API response.

    - One entry per configured service, in configuration order.
    - A service without a record is `unknown` with null responseMs and lastChecked.
    - Records for services that are not configured are ignored.
    - Events for removed services are kept. Expired events (expiresAt <= now) are dropped.
    - Events are sorted newest first by (at, serviceId) and cut to MAX_EVENTS.

    Raises InvalidStoredData if a record of a configured service or any event is invalid.
    """
    configured_ids = {service.id for service in services}

    stored: dict[str, dict[str, Any]] = {}
    for item in records:
        service_id = item.get("id")
        if not isinstance(service_id, str) or service_id not in configured_ids:
            continue
        stored[service_id] = validate_service_record(item, require_counters=False)

    service_entries = []
    for service in services:
        record = stored.get(service.id)
        if record is None:
            entry = {"id": service.id, "name": service.name, "status": "unknown",
                     "responseMs": None, "lastChecked": None}
        else:
            entry = {"id": service.id, "name": service.name, "status": record["status"],
                     "responseMs": record["responseMs"], "lastChecked": record["lastChecked"]}
        service_entries.append({field: entry[field] for field in SERVICE_FIELDS})

    now_epoch = int(now.timestamp())
    live_events = [event for event in map(validate_event, events) if event["expiresAt"] > now_epoch]
    live_events.sort(key=lambda event: (event["at"], event["serviceId"]), reverse=True)
    event_entries = [{field: event[field] for field in EVENT_FIELDS} for event in live_events[:MAX_EVENTS]]

    return {"services": service_entries, "events": event_entries}


__all__ = ["EVENT_FIELDS", "MAX_EVENTS", "SERVICE_FIELDS", "InvalidStoredData", "build_status_response"]
