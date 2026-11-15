"""Tests for ghostpet/checker_items.py.

Covers build_check_result, build_service_record, build_event_item, and
build_idempotency_token.  No I/O, no boto3, no network calls.

Design contract references: .kiro/specs/state-store-read-api/design.md
sections 2.1, 2.2, 4.1 (step 5), 4.2, 4.3, 4.4.
"""

from __future__ import annotations

import pytest

from ghostpet.checker_items import (
    build_check_result,
    build_event_item,
    build_idempotency_token,
    build_service_record,
)
from ghostpet.decision import INITIAL_TRACKER, Transition
from ghostpet.model import (
    EVENTS_PK,
    EVENT_SK_PREFIX,
    SERVICES_PK,
    SERVICE_SK_PREFIX,
    InvalidStoredData,
    expires_at,
    idempotency_token,
    parse_ts,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CHECKED_AT = "2026-11-15T02:00:00Z"
SERVICE_ID = "site-1"
SERVICE_NAME = "Example"
RETENTION_DAYS = 7

FAST_TRANSITION = Transition(from_status="unknown", to="healthy", note="first fast check")
SLOW_TRANSITION = Transition(from_status="healthy", to="slow", note="response over 2s twice in a row")
DOWN_TRANSITION = Transition(from_status="healthy", to="down", note="3 failed checks in a row")
RECOVERING_TRANSITION = Transition(from_status="down", to="recovering", note="first successful check after outage")


# ---------------------------------------------------------------------------
# build_check_result
# ---------------------------------------------------------------------------


class TestBuildCheckResult:
    def test_200_fast(self):
        r = build_check_result(status_code=200, response_ms=150, timed_out=False)
        assert r["statusCode"] == 200
        assert r["responseMs"] == 150
        assert r["timedOut"] is False

    def test_timeout(self):
        r = build_check_result(status_code=None, response_ms=5000, timed_out=True)
        assert r["statusCode"] is None
        assert r["responseMs"] == 5000
        assert r["timedOut"] is True

    def test_connection_error_no_timeout(self):
        """Connection refused: statusCode=None, timedOut=False."""
        r = build_check_result(status_code=None, response_ms=0, timed_out=False)
        assert r["statusCode"] is None
        assert r["timedOut"] is False

    def test_503(self):
        r = build_check_result(status_code=503, response_ms=90, timed_out=False)
        assert r["statusCode"] == 503
        assert r["timedOut"] is False

    def test_301_redirect(self):
        """3xx is a valid success (design 4.1 / petState.ts test 16)."""
        r = build_check_result(status_code=301, response_ms=100, timed_out=False)
        assert r["statusCode"] == 301

    def test_response_ms_zero(self):
        r = build_check_result(status_code=200, response_ms=0, timed_out=False)
        assert r["responseMs"] == 0

    # --- validation errors ---

    def test_negative_response_ms_raises(self):
        with pytest.raises(ValueError, match="response_ms"):
            build_check_result(status_code=200, response_ms=-1, timed_out=False)

    def test_bool_response_ms_raises(self):
        with pytest.raises(ValueError, match="response_ms"):
            build_check_result(status_code=200, response_ms=True, timed_out=False)  # type: ignore[arg-type]

    def test_non_bool_timed_out_raises(self):
        with pytest.raises(ValueError, match="timed_out"):
            build_check_result(status_code=None, response_ms=5000, timed_out=1)  # type: ignore[arg-type]

    def test_timed_out_with_status_code_raises(self):
        """timedOut=True must have statusCode=None (no HTTP response on timeout)."""
        with pytest.raises(ValueError, match="None when timed_out"):
            build_check_result(status_code=200, response_ms=5000, timed_out=True)

    def test_status_code_below_100_raises(self):
        with pytest.raises(ValueError, match="status_code"):
            build_check_result(status_code=99, response_ms=50, timed_out=False)

    def test_bool_status_code_raises(self):
        with pytest.raises(ValueError, match="status_code"):
            build_check_result(status_code=True, response_ms=50, timed_out=False)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_service_record
# ---------------------------------------------------------------------------

def _base_record(**overrides):
    """Return kwargs for build_service_record with sensible defaults."""
    defaults = dict(
        service_id=SERVICE_ID,
        service_name=SERVICE_NAME,
        status="healthy",
        response_ms=150,
        checked_at=CHECKED_AT,
        consecutive_failures=0,
        consecutive_slow=0,
        consecutive_fast=1,
        expected_version=None,
    )
    defaults.update(overrides)
    return defaults


class TestBuildServiceRecord:

    # --- structure and keys ---

    def test_pk_sk_first_write(self):
        rec = build_service_record(**_base_record())
        assert rec["pk"] == SERVICES_PK
        assert rec["sk"] == SERVICE_SK_PREFIX + SERVICE_ID

    def test_all_expected_fields_present(self):
        rec = build_service_record(**_base_record())
        expected_keys = {
            "pk", "sk", "id", "name", "status",
            "responseMs", "lastChecked",
            "consecutiveFailures", "consecutiveSlow", "consecutiveFast",
            "version",
        }
        assert set(rec.keys()) == expected_keys

    def test_no_expiresAt(self):
        """Service records never expire (design 2.1)."""
        rec = build_service_record(**_base_record())
        assert "expiresAt" not in rec

    def test_field_values(self):
        rec = build_service_record(**_base_record(
            status="slow", response_ms=2300,
            consecutive_slow=2, consecutive_failures=0, consecutive_fast=0,
            expected_version=5,
        ))
        assert rec["id"] == SERVICE_ID
        assert rec["name"] == SERVICE_NAME
        assert rec["status"] == "slow"
        assert rec["responseMs"] == 2300
        assert rec["lastChecked"] == CHECKED_AT
        assert rec["consecutiveSlow"] == 2
        assert rec["consecutiveFailures"] == 0
        assert rec["consecutiveFast"] == 0

    # --- version counter (design 4.2) ---

    def test_first_write_version_is_1(self):
        rec = build_service_record(**_base_record(expected_version=None))
        assert rec["version"] == 1

    def test_subsequent_write_increments_version(self):
        rec = build_service_record(**_base_record(expected_version=41))
        assert rec["version"] == 42

    def test_version_1_increments_to_2(self):
        rec = build_service_record(**_base_record(expected_version=1))
        assert rec["version"] == 2

    # --- all five statuses ---

    @pytest.mark.parametrize("status", ["unknown", "healthy", "slow", "down", "recovering"])
    def test_all_valid_statuses(self, status):
        rec = build_service_record(**_base_record(status=status))
        assert rec["status"] == status

    # --- validation (design 4.1 step 5) ---

    def test_invalid_status_raises(self):
        with pytest.raises(InvalidStoredData):
            build_service_record(**_base_record(status="Healthy"))

    def test_unknown_status_raises(self):
        with pytest.raises(InvalidStoredData):
            build_service_record(**_base_record(status="broken"))

    def test_negative_response_ms_raises(self):
        with pytest.raises(InvalidStoredData):
            build_service_record(**_base_record(response_ms=-1))

    def test_negative_counter_raises(self):
        with pytest.raises(InvalidStoredData):
            build_service_record(**_base_record(consecutive_failures=-1))

    def test_bad_timestamp_raises(self):
        with pytest.raises(InvalidStoredData):
            build_service_record(**_base_record(checked_at="2026-11-15 02:00:00"))

    def test_timestamp_with_fractional_seconds_raises(self):
        with pytest.raises(InvalidStoredData):
            build_service_record(**_base_record(checked_at="2026-11-15T02:00:00.123Z"))

    def test_initial_tracker_counters(self):
        """Counters from INITIAL_TRACKER (all zero) produce a valid record."""
        rec = build_service_record(**_base_record(
            consecutive_failures=INITIAL_TRACKER["consecutiveFailures"],
            consecutive_slow=INITIAL_TRACKER["consecutiveSlow"],
            consecutive_fast=INITIAL_TRACKER["consecutiveFast"],
        ))
        assert rec["consecutiveFailures"] == 0
        assert rec["consecutiveSlow"] == 0
        assert rec["consecutiveFast"] == 0

    def test_response_ms_zero_is_valid(self):
        """0 ms is a valid (non-negative) response time."""
        rec = build_service_record(**_base_record(response_ms=0))
        assert rec["responseMs"] == 0


# ---------------------------------------------------------------------------
# build_event_item
# ---------------------------------------------------------------------------

def _base_event(**overrides):
    defaults = dict(
        service_id=SERVICE_ID,
        transition=FAST_TRANSITION,
        checked_at=CHECKED_AT,
        retention_days=RETENTION_DAYS,
    )
    defaults.update(overrides)
    return defaults


class TestBuildEventItem:

    # --- structure and keys (design 2.2) ---

    def test_pk_is_events(self):
        item = build_event_item(**_base_event())
        assert item["pk"] == EVENTS_PK

    def test_sk_format(self):
        """sk = EVENT#<at>#<serviceId> (design 2.2)."""
        item = build_event_item(**_base_event())
        expected_sk = f"{EVENT_SK_PREFIX}{CHECKED_AT}#{SERVICE_ID}"
        assert item["sk"] == expected_sk

    def test_all_expected_fields_present(self):
        item = build_event_item(**_base_event())
        expected_keys = {"pk", "sk", "serviceId", "from", "to", "at", "note", "expiresAt"}
        assert set(item.keys()) == expected_keys

    def test_field_values(self):
        item = build_event_item(**_base_event())
        assert item["serviceId"] == SERVICE_ID
        assert item["from"] == "unknown"   # from_status → "from" mapping
        assert item["to"] == "healthy"
        assert item["at"] == CHECKED_AT
        assert item["note"] == "first fast check"

    # --- from_status → "from" key mapping ---

    def test_from_key_not_from_status(self):
        """The DynamoDB item must use 'from', never 'from_status'."""
        item = build_event_item(**_base_event())
        assert "from" in item
        assert "from_status" not in item

    @pytest.mark.parametrize("transition,expected_from,expected_to", [
        (FAST_TRANSITION, "unknown", "healthy"),
        (SLOW_TRANSITION, "healthy", "slow"),
        (DOWN_TRANSITION, "healthy", "down"),
        (RECOVERING_TRANSITION, "down", "recovering"),
        (Transition(from_status="recovering", to="down", note="failed again while recovering"),
         "recovering", "down"),
        (Transition(from_status="slow", to="healthy", note="response at or under 2s twice in a row"),
         "slow", "healthy"),
    ])
    def test_from_to_mapping_all_transitions(self, transition, expected_from, expected_to):
        item = build_event_item(**_base_event(transition=transition))
        assert item["from"] == expected_from
        assert item["to"] == expected_to

    # --- expiresAt (design 2.2, 4.4) ---

    def test_expires_at_7_days(self):
        item = build_event_item(**_base_event(retention_days=7))
        expected = expires_at(CHECKED_AT, 7)
        assert item["expiresAt"] == expected

    def test_expires_at_1_day(self):
        item = build_event_item(**_base_event(retention_days=1))
        epoch = int(parse_ts(CHECKED_AT).timestamp())
        assert item["expiresAt"] == epoch + 86400

    def test_expires_at_30_days(self):
        item = build_event_item(**_base_event(retention_days=30))
        epoch = int(parse_ts(CHECKED_AT).timestamp())
        assert item["expiresAt"] == epoch + 30 * 86400

    def test_expires_at_adds_604800_for_7_days(self):
        """7 days = 604 800 s (design test plan section 9 / test_model.py contract)."""
        item = build_event_item(**_base_event(retention_days=7))
        epoch = int(parse_ts(CHECKED_AT).timestamp())
        assert item["expiresAt"] == epoch + 604_800

    # --- validation (design 4.1 step 5) ---

    def test_same_from_to_raises(self):
        """from == to is not a real transition; validate_event must reject it."""
        bad = Transition(from_status="healthy", to="healthy", note="no change")
        with pytest.raises(InvalidStoredData):
            build_event_item(**_base_event(transition=bad))

    def test_invalid_from_status_raises(self):
        bad = Transition(from_status="Healthy", to="slow", note="note")
        with pytest.raises(InvalidStoredData):
            build_event_item(**_base_event(transition=bad))

    def test_invalid_to_status_raises(self):
        bad = Transition(from_status="healthy", to="broken", note="note")
        with pytest.raises(InvalidStoredData):
            build_event_item(**_base_event(transition=bad))

    def test_note_over_200_chars_raises(self):
        bad = Transition(from_status="healthy", to="slow", note="x" * 201)
        with pytest.raises(InvalidStoredData):
            build_event_item(**_base_event(transition=bad))

    def test_note_exactly_200_chars_is_valid(self):
        ok = Transition(from_status="healthy", to="slow", note="x" * 200)
        item = build_event_item(**_base_event(transition=ok))
        assert len(item["note"]) == 200

    def test_bad_checked_at_raises(self):
        with pytest.raises((InvalidStoredData, ValueError)):
            build_event_item(**_base_event(checked_at="not-a-timestamp"))

    def test_zero_retention_days_raises(self):
        with pytest.raises(ValueError):
            build_event_item(**_base_event(retention_days=0))

    def test_negative_retention_days_raises(self):
        with pytest.raises(ValueError):
            build_event_item(**_base_event(retention_days=-1))

    # --- no counters, pk/sk exclusion not needed for events, but no extra fields ---

    def test_no_version_field(self):
        item = build_event_item(**_base_event())
        assert "version" not in item

    def test_no_counter_fields(self):
        item = build_event_item(**_base_event())
        for field in ("consecutiveFailures", "consecutiveSlow", "consecutiveFast"):
            assert field not in item

    # --- sk is idempotency key (design 4.4) ---

    def test_sk_matches_event_sk_helper(self):
        from ghostpet.model import event_sk
        item = build_event_item(**_base_event())
        assert item["sk"] == event_sk(CHECKED_AT, SERVICE_ID)

    def test_sk_uniqueness_per_service(self):
        item_a = build_event_item(**_base_event(service_id="site-1"))
        item_b = build_event_item(**_base_event(service_id="site-2"))
        assert item_a["sk"] != item_b["sk"]

    def test_sk_uniqueness_per_time(self):
        item_a = build_event_item(**_base_event(checked_at="2026-11-15T02:00:00Z"))
        item_b = build_event_item(**_base_event(checked_at="2026-11-15T02:01:00Z"))
        assert item_a["sk"] != item_b["sk"]


# ---------------------------------------------------------------------------
# build_idempotency_token
# ---------------------------------------------------------------------------


class TestBuildIdempotencyToken:

    def test_returns_string(self):
        token = build_idempotency_token(SERVICE_ID, CHECKED_AT)
        assert isinstance(token, str)

    def test_length_is_36(self):
        """ClientRequestToken must be exactly 36 characters (design 4.3)."""
        token = build_idempotency_token(SERVICE_ID, CHECKED_AT)
        assert len(token) == 36

    def test_stable_same_inputs(self):
        """Same (serviceId, checkedAt) always produces the same token."""
        t1 = build_idempotency_token(SERVICE_ID, CHECKED_AT)
        t2 = build_idempotency_token(SERVICE_ID, CHECKED_AT)
        assert t1 == t2

    def test_differs_per_service(self):
        t1 = build_idempotency_token("site-1", CHECKED_AT)
        t2 = build_idempotency_token("site-2", CHECKED_AT)
        assert t1 != t2

    def test_differs_per_time(self):
        t1 = build_idempotency_token(SERVICE_ID, "2026-11-15T02:00:00Z")
        t2 = build_idempotency_token(SERVICE_ID, "2026-11-15T02:01:00Z")
        assert t1 != t2

    def test_matches_model_idempotency_token(self):
        """build_idempotency_token is a thin re-export of model.idempotency_token."""
        assert build_idempotency_token(SERVICE_ID, CHECKED_AT) == idempotency_token(SERVICE_ID, CHECKED_AT)

    def test_uuid4_format(self):
        """Token is a UUID (8-4-4-4-12 hex format)."""
        import re
        token = build_idempotency_token(SERVICE_ID, CHECKED_AT)
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            token,
        )


# ---------------------------------------------------------------------------
# Integration: build_service_record + build_event_item together
# ---------------------------------------------------------------------------


class TestRecordAndEventTogether:
    """Verifies that a record and its corresponding event share consistent data."""

    def test_service_record_and_event_share_service_id(self):
        rec = build_service_record(**_base_record())
        item = build_event_item(**_base_event())
        assert rec["id"] == item["serviceId"]

    def test_service_record_and_event_share_checked_at(self):
        rec = build_service_record(**_base_record())
        item = build_event_item(**_base_event())
        assert rec["lastChecked"] == item["at"]

    def test_event_to_matches_record_status(self):
        """The event 'to' field must match the new status in the service record."""
        rec = build_service_record(**_base_record(status="slow"))
        item = build_event_item(**_base_event(transition=SLOW_TRANSITION))
        assert item["to"] == rec["status"]

    def test_different_pk_for_record_and_event(self):
        """Record and event go to different DynamoDB partitions."""
        rec = build_service_record(**_base_record())
        item = build_event_item(**_base_event())
        assert rec["pk"] != item["pk"]
        assert rec["pk"] == SERVICES_PK
        assert item["pk"] == EVENTS_PK

    def test_idempotency_token_matches_event_sk_inputs(self):
        """ClientRequestToken is derived from the same (serviceId, checkedAt) as the event sk."""
        item = build_event_item(**_base_event())
        token = build_idempotency_token(SERVICE_ID, CHECKED_AT)
        # Both are deterministic from the same pair.
        assert len(token) == 36
        assert item["sk"].endswith(f"#{SERVICE_ID}")
        assert CHECKED_AT in item["sk"]
