from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ghostpet import model
from ghostpet.model import (
    EVENTS_PK,
    SERVICES_PK,
    InvalidStoredData,
    event_sk,
    expires_at,
    format_ts,
    idempotency_token,
    parse_ts,
    service_sk,
    validate_event,
    validate_service_record,
)

AT = "2026-11-15T01:58:00Z"


def full_record(**overrides):
    item = {"id": "site-1", "status": "healthy", "responseMs": 84, "lastChecked": AT,
            "consecutiveFailures": 0, "consecutiveSlow": 0, "consecutiveFast": 3}
    item.update(overrides)
    return item


def full_event(**overrides):
    item = {"serviceId": "site-1", "from": "healthy", "to": "slow", "at": AT,
            "note": "response over 2s twice in a row", "expiresAt": 1795312680}
    item.update(overrides)
    return item


# --- keys ---------------------------------------------------------------------


def test_partition_keys():
    assert (SERVICES_PK, EVENTS_PK) == ("SERVICES", "EVENTS")


def test_service_sk():
    assert service_sk("site-1") == "SERVICE#site-1"


def test_event_sk_matches_design():
    assert event_sk(AT, "site-1") == "EVENT#2026-11-15T01:58:00Z#site-1"


def test_event_sk_string_order_equals_time_order():
    keys = [event_sk(format_ts(datetime(2026, 11, d, h, tzinfo=UTC)), "s") for d in (9, 15) for h in (3, 23)]
    assert keys == sorted(keys)


@pytest.mark.parametrize("bad", ["", "a#b", None])
def test_key_builders_reject_bad_service_ids(bad):
    with pytest.raises(ValueError):
        service_sk(bad)
    with pytest.raises(ValueError):
        event_sk(AT, bad)


def test_event_sk_rejects_bad_timestamp():
    with pytest.raises(ValueError):
        event_sk("2026-11-15T01:58:00.000Z", "site-1")


# --- timestamps ---------------------------------------------------------------


def test_format_parse_round_trip():
    assert format_ts(parse_ts(AT)) == AT


def test_format_ts_converts_to_utc_and_drops_fraction():
    jst = timezone(timedelta(hours=9))
    assert format_ts(datetime(2026, 11, 15, 10, 58, 0, 999999, tzinfo=jst)) == AT


def test_format_ts_rejects_naive_datetime():
    with pytest.raises(ValueError):
        format_ts(datetime(2026, 11, 15, 1, 58))


@pytest.mark.parametrize("bad", ["2026-11-15T01:58:00+00:00", "2026-11-15T01:58:00.5Z", "2026-02-30T00:00:00Z", 0])
def test_parse_ts_rejects_other_formats(bad):
    with pytest.raises(ValueError):
        parse_ts(bad)


# --- TTL ----------------------------------------------------------------------


def test_expires_at_seven_days_adds_604800_seconds():
    assert expires_at(AT, 7) - int(parse_ts(AT).timestamp()) == 604_800
    assert expires_at(AT, 7) == 1795312680


def test_expires_at_is_adjustable():
    assert expires_at(AT, 1) - int(parse_ts(AT).timestamp()) == 86_400


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "7"])
def test_expires_at_rejects_bad_retention(bad):
    with pytest.raises(ValueError):
        expires_at(AT, bad)


# --- idempotency token --------------------------------------------------------


def test_idempotency_token_is_stable_and_36_chars():
    token = idempotency_token("site-1", AT)
    assert token == idempotency_token("site-1", AT)
    assert len(token) == 36


def test_idempotency_token_differs_per_service_and_time():
    tokens = {
        idempotency_token("site-1", AT),
        idempotency_token("site-2", AT),
        idempotency_token("site-1", "2026-11-15T01:59:00Z"),
    }
    assert len(tokens) == 3


def test_idempotency_token_rejects_bad_input():
    with pytest.raises(ValueError):
        idempotency_token("site-1", "2026-11-15T01:58:00")
    with pytest.raises(ValueError):
        idempotency_token("a#b", AT)


# --- validators (writer side) -------------------------------------------------


def test_valid_record_and_event_pass_and_normalize_decimals():
    record = validate_service_record(full_record(responseMs=Decimal(84), consecutiveFast=Decimal(3)))
    assert record["responseMs"] == 84 and type(record["consecutiveFast"]) is int
    assert validate_event(full_event(expiresAt=Decimal(1795312680)))["expiresAt"] == 1795312680


@pytest.mark.parametrize("field", list(model.COUNTER_FIELDS))
def test_writer_validation_rejects_negative_counters(field):
    with pytest.raises(InvalidStoredData) as exc:
        validate_service_record(full_record(**{field: -1}))
    assert exc.value.field == field


@pytest.mark.parametrize("field", list(model.COUNTER_FIELDS))
def test_writer_validation_requires_counters(field):
    item = full_record()
    del item[field]
    with pytest.raises(InvalidStoredData):
        validate_service_record(item)


def test_writer_validation_rejects_bad_status():
    with pytest.raises(InvalidStoredData):
        validate_service_record(full_record(status="Down"))


def test_event_validation_rejects_identical_from_and_to():
    with pytest.raises(InvalidStoredData) as exc:
        validate_event(full_event(to="healthy"))
    assert exc.value.field == "to"


def test_event_validation_rejects_long_note_and_missing_ttl():
    with pytest.raises(InvalidStoredData):
        validate_event(full_event(note="x" * 201))
    item = full_event()
    del item["expiresAt"]
    with pytest.raises(InvalidStoredData):
        validate_event(item)


def test_event_validation_rejects_missing_service_id():
    with pytest.raises(InvalidStoredData) as exc:
        validate_event(full_event(serviceId=""))
    assert exc.value.field == "serviceId"
