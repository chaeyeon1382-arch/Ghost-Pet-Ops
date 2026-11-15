import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ghostpet.model import STATUSES, InvalidStoredData, format_ts
from ghostpet.status_response import MAX_EVENTS, build_status_response

NOW = datetime(2026, 11, 15, 2, 0, 0, tzinfo=UTC)
NOW_EPOCH = int(NOW.timestamp())
WEEK = 7 * 86400
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class Svc:
    id: str
    name: str
    url: str = "https://example.com/"


SERVICES = [Svc("site-1", "Example"), Svc("site-2", "Example Two"), Svc("site-3", "Example Three")]


def record(service_id="site-1", **overrides):
    """A stored service record as DynamoDB returns it (numbers as Decimal, internal keys included)."""
    item = {
        "pk": "SERVICES",
        "sk": f"SERVICE#{service_id}",
        "id": service_id,
        "name": "Stored name",
        "status": "healthy",
        "responseMs": Decimal(84),
        "lastChecked": "2026-11-15T02:00:00Z",
        "consecutiveFailures": Decimal(0),
        "consecutiveSlow": Decimal(0),
        "consecutiveFast": Decimal(3),
        "version": Decimal(41),
    }
    item.update(overrides)
    return item


def event(minutes_ago=0, service_id="site-1", frm="healthy", to="slow", **overrides):
    at = format_ts(NOW - timedelta(minutes=minutes_ago))
    item = {
        "pk": "EVENTS",
        "sk": f"EVENT#{at}#{service_id}",
        "serviceId": service_id,
        "from": frm,
        "to": to,
        "at": at,
        "note": "response over 2s twice in a row",
        "expiresAt": Decimal(int((NOW - timedelta(minutes=minutes_ago)).timestamp()) + WEEK),
    }
    item.update(overrides)
    return item


def build(services=SERVICES, records=(), events=()):
    return build_status_response(services, list(records), list(events), NOW)


def all_keys(value):
    """Every dict key anywhere in a JSON-like structure."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in all_keys(v)}
    return set()


# --- unknown defaults ---------------------------------------------------------


def test_service_without_record_is_unknown_with_null_fields():
    result = build(services=[Svc("site-1", "Example")])
    assert result == {
        "services": [
            {"id": "site-1", "name": "Example", "status": "unknown", "responseMs": None, "lastChecked": None}
        ],
        "events": [],
    }


def test_mixed_stored_and_missing_services_keep_config_order():
    records = [record("site-3", status="down", responseMs=Decimal(5000)), record("site-1")]
    result = build(records=records)
    assert [s["id"] for s in result["services"]] == ["site-1", "site-2", "site-3"]
    assert result["services"][0] == {
        "id": "site-1", "name": "Example", "status": "healthy", "responseMs": 84,
        "lastChecked": "2026-11-15T02:00:00Z",
    }
    assert result["services"][1] == {
        "id": "site-2", "name": "Example Two", "status": "unknown", "responseMs": None, "lastChecked": None,
    }
    assert result["services"][2]["status"] == "down"
    assert result["services"][2]["responseMs"] == 5000


def test_name_comes_from_config_not_from_stored_record():
    result = build(records=[record("site-1", name="Old name")])
    assert result["services"][0]["name"] == "Example"


def test_stored_unknown_status_keeps_its_check_values():
    result = build(records=[record("site-1", status="unknown", responseMs=Decimal(2300))])
    assert result["services"][0]["status"] == "unknown"
    assert result["services"][0]["responseMs"] == 2300


def test_records_for_unconfigured_services_are_ignored():
    result = build(records=[record("removed-site"), record("site-2")])
    assert [s["id"] for s in result["services"]] == ["site-1", "site-2", "site-3"]
    assert result["services"][1]["status"] == "healthy"


def test_invalid_record_for_unconfigured_service_is_ignored():
    result = build(records=[record("removed-site", status="broken")])
    assert result["services"][0]["status"] == "unknown"


# --- event ordering and limit -------------------------------------------------


def test_events_are_newest_first_even_if_given_out_of_order():
    events = [event(10), event(1), event(5), event(3)]
    ats = [e["at"] for e in build(events=events)["events"]]
    assert ats == sorted(ats, reverse=True)
    assert ats[0] == format_ts(NOW - timedelta(minutes=1))


def test_events_with_equal_at_are_ordered_by_service_id_descending():
    events = [event(2, "site-1"), event(2, "site-3"), event(2, "site-2")]
    assert [e["serviceId"] for e in build(events=events)["events"]] == ["site-3", "site-2", "site-1"]


def test_at_most_20_events_newest_kept():
    events = [event(minutes) for minutes in range(25)]
    result = build(events=events)["events"]
    assert MAX_EVENTS == 20
    assert len(result) == 20
    assert result[0]["at"] == format_ts(NOW)
    assert result[-1]["at"] == format_ts(NOW - timedelta(minutes=19))


def test_fewer_than_20_events_returned_as_is():
    assert len(build(events=[event(1), event(2)])["events"]) == 2


def test_events_for_removed_services_are_kept():
    result = build(events=[event(1, "removed-site")])
    assert result["events"][0]["serviceId"] == "removed-site"


def test_expired_events_are_dropped():
    expired = event(1, expiresAt=Decimal(NOW_EPOCH))  # expiresAt <= now counts as expired
    live = event(2, expiresAt=Decimal(NOW_EPOCH + 1))
    result = build(events=[expired, live])["events"]
    assert [e["at"] for e in result] == [live["at"]]


# --- status names -------------------------------------------------------------


def test_all_statuses_in_output_are_lowercase_known_values():
    records = [record("site-1", status="slow"), record("site-2", status="recovering")]
    events = [event(1, frm="unknown", to="healthy"), event(2, frm="down", to="recovering")]
    result = build(records=records, events=events)
    values = [s["status"] for s in result["services"]]
    values += [e[k] for e in result["events"] for k in ("from", "to")]
    assert values and all(v in STATUSES and v == v.lower() for v in values)


@pytest.mark.parametrize("bad", ["Healthy", "HEALTHY", "broken", "", None, Decimal(1)])
def test_invalid_stored_status_raises_with_service_id(bad):
    with pytest.raises(InvalidStoredData) as exc:
        build(records=[record("site-2", status=bad)])
    assert exc.value.service_id == "site-2"
    assert exc.value.field == "status"


@pytest.mark.parametrize("field", ["from", "to"])
def test_invalid_event_status_raises_with_service_id(field):
    with pytest.raises(InvalidStoredData) as exc:
        build(events=[event(1, "site-3", **{field: "Slow"})])
    assert (exc.value.service_id, exc.value.field) == ("site-3", field)


# --- timestamps ---------------------------------------------------------------


def test_timestamps_are_utc_iso8601_with_z():
    result = build(records=[record("site-1")], events=[event(1), event(30)])
    stamps = [s["lastChecked"] for s in result["services"] if s["lastChecked"] is not None]
    stamps += [e["at"] for e in result["events"]]
    assert len(stamps) == 3
    assert all(TS_RE.match(s) for s in stamps)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-11-15T02:00:00+00:00",
        "2026-11-15T11:00:00+09:00",
        "2026-11-15T02:00:00.123Z",
        "2026-11-15 02:00:00Z",
        "2026-11-15T02:00:00",
        "2026-13-01T00:00:00Z",
        "",
        None,
        Decimal(1795312680),
    ],
)
def test_invalid_stored_timestamp_raises(bad):
    with pytest.raises(InvalidStoredData) as exc:
        build(records=[record("site-1", lastChecked=bad)])
    assert (exc.value.service_id, exc.value.field) == ("site-1", "lastChecked")
    with pytest.raises(InvalidStoredData) as exc:
        build(events=[event(1, "site-2", at=bad)])
    assert (exc.value.service_id, exc.value.field) == ("site-2", "at")


# --- no leaks -----------------------------------------------------------------


def test_service_and_event_entries_have_exactly_the_public_keys():
    result = build(records=[record("site-1")], events=[event(1)])
    assert set(result) == {"services", "events"}
    for service in result["services"]:
        assert list(service) == ["id", "name", "status", "responseMs", "lastChecked"]
    assert list(result["events"][0]) == ["serviceId", "from", "to", "at", "note"]


def test_counters_and_internal_attributes_never_appear():
    records = [record(sid, extra="x") for sid in ("site-1", "site-2", "removed")]
    result = build(records=records, events=[event(1), event(2, "removed")])
    forbidden = {"consecutiveFailures", "consecutiveSlow", "consecutiveFast",
                 "pk", "sk", "version", "expiresAt", "extra", "url"}
    assert all_keys(result).isdisjoint(forbidden)
    serialized = json.dumps(result)
    assert all(name not in serialized for name in forbidden)


# --- number handling ----------------------------------------------------------


def test_decimal_numbers_become_int_and_serialize_as_json():
    result = build(records=[record("site-1", responseMs=Decimal(2300))])
    assert type(result["services"][0]["responseMs"]) is int
    json.dumps(result)  # would raise TypeError on Decimal


@pytest.mark.parametrize("bad", [Decimal(-1), Decimal("1.5"), "84", True, None, Decimal("NaN")])
def test_invalid_response_ms_raises(bad):
    with pytest.raises(InvalidStoredData) as exc:
        build(records=[record("site-1", responseMs=bad)])
    assert exc.value.field == "responseMs"


def test_counters_are_not_required_in_reader_projection():
    projected = {"id": "site-1", "status": "slow", "responseMs": Decimal(2400), "lastChecked": "2026-11-15T02:00:00Z"}
    assert build(records=[projected])["services"][0]["status"] == "slow"


def test_input_items_are_not_modified():
    rec, ev = record("site-1"), event(1)
    before = (dict(rec), dict(ev))
    build(records=[rec], events=[ev])
    assert (rec, ev) == before
