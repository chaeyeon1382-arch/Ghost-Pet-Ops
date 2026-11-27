"""Tests for ghostpet/checker_writer.py.

No real AWS calls are made.  Every test injects a fake DynamoDB Table object
whose get_item / update_item / meta.client.transact_write_items methods are
controlled via unittest.mock.

Design contract references: .kiro/specs/state-store-read-api/design.md 4.1–4.5.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

from ghostpet.checker_writer import (
    MAX_ATTEMPTS,
    WriteOutcome,
    _dynamo_typed,
    _extract_tracker,
    _get_cancellation_reasons,
    _handle_txn_cancel,
    _is_stale,
    write_check_result,
)
from ghostpet.config import Service
from ghostpet.decision import INITIAL_TRACKER, CheckResult
from ghostpet.model import EVENTS_PK, SERVICES_PK, event_sk, service_sk

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SERVICE = Service(id="site-1", name="Example", url="https://example.com/")
CHECKED_AT = "2026-11-15T02:00:00Z"
RETENTION = 7

FAST_CHECK = CheckResult(statusCode=200, responseMs=150, timedOut=False)
SLOW_CHECK = CheckResult(statusCode=200, responseMs=2300, timedOut=False)
TIMEOUT_CHECK = CheckResult(statusCode=None, responseMs=5000, timedOut=True)
FAIL_CHECK = CheckResult(statusCode=503, responseMs=90, timedOut=False)


def _make_table(*, get_item_return=None, update_raises=None, transact_raises=None):
    """Build a minimal fake DynamoDB Table resource."""
    table = MagicMock()
    table.name = "ghost-pet-state"

    if get_item_return is None:
        table.get_item.return_value = {"Item": None}
    else:
        table.get_item.return_value = {"Item": get_item_return}

    if update_raises:
        table.update_item.side_effect = update_raises
    else:
        table.update_item.return_value = {}

    if transact_raises:
        table.meta.client.transact_write_items.side_effect = transact_raises
    else:
        table.meta.client.transact_write_items.return_value = {}

    return table


def _stored_record(
    *,
    status="healthy",
    last_checked="2026-11-15T01:59:00Z",
    version=5,
    consecutive_failures=0,
    consecutive_slow=0,
    consecutive_fast=1,
    response_ms=100,
):
    return {
        "pk": SERVICES_PK,
        "sk": service_sk(SERVICE.id),
        "id": SERVICE.id,
        "name": SERVICE.name,
        "status": status,
        "responseMs": Decimal(str(response_ms)),
        "lastChecked": last_checked,
        "consecutiveFailures": Decimal(str(consecutive_failures)),
        "consecutiveSlow": Decimal(str(consecutive_slow)),
        "consecutiveFast": Decimal(str(consecutive_fast)),
        "version": Decimal(str(version)),
    }


def _make_txn_cancel(reasons: list[str]) -> Exception:
    """Build a fake TransactionCanceledException with the given per-item reason codes."""
    exc = Exception("TransactionCanceledException")
    exc.response = {
        "Error": {"Code": "TransactionCanceledException"},
        "CancellationReasons": [{"Code": c} for c in reasons],
    }
    return exc


def _make_client_error(code: str) -> Exception:
    exc = Exception(code)
    exc.response = {"Error": {"Code": code}}
    return exc


# ---------------------------------------------------------------------------
# _is_stale
# ---------------------------------------------------------------------------

class TestIsStale:
    def test_no_item_not_stale(self):
        assert _is_stale(None, CHECKED_AT) is False

    def test_older_last_checked_not_stale(self):
        item = {"lastChecked": "2026-11-15T01:59:00Z"}
        assert _is_stale(item, "2026-11-15T02:00:00Z") is False

    def test_equal_last_checked_is_stale(self):
        item = {"lastChecked": "2026-11-15T02:00:00Z"}
        assert _is_stale(item, "2026-11-15T02:00:00Z") is True

    def test_newer_last_checked_is_stale(self):
        item = {"lastChecked": "2026-11-15T02:01:00Z"}
        assert _is_stale(item, "2026-11-15T02:00:00Z") is True

    def test_missing_last_checked_not_stale(self):
        assert _is_stale({}, CHECKED_AT) is False

    def test_non_string_last_checked_not_stale(self):
        assert _is_stale({"lastChecked": 12345}, CHECKED_AT) is False


# ---------------------------------------------------------------------------
# _extract_tracker
# ---------------------------------------------------------------------------

class TestExtractTracker:
    def test_none_gives_initial_tracker(self):
        t = _extract_tracker(None)
        assert t == INITIAL_TRACKER

    def test_decimal_values_converted_to_int(self):
        item = {
            "status": "healthy",
            "consecutiveFailures": Decimal("0"),
            "consecutiveSlow": Decimal("1"),
            "consecutiveFast": Decimal("3"),
        }
        t = _extract_tracker(item)
        assert t["consecutiveFailures"] == 0
        assert t["consecutiveSlow"] == 1
        assert t["consecutiveFast"] == 3
        assert isinstance(t["consecutiveFailures"], int)

    def test_status_preserved(self):
        item = {
            "status": "slow",
            "consecutiveFailures": Decimal("0"),
            "consecutiveSlow": Decimal("2"),
            "consecutiveFast": Decimal("0"),
        }
        assert _extract_tracker(item)["status"] == "slow"

    def test_missing_counters_default_to_zero(self):
        t = _extract_tracker({"status": "down"})
        assert t["consecutiveFailures"] == 0
        assert t["consecutiveSlow"] == 0
        assert t["consecutiveFast"] == 0


# ---------------------------------------------------------------------------
# _dynamo_typed
# ---------------------------------------------------------------------------

class TestDynamoTyped:
    def test_string_field(self):
        assert _dynamo_typed({"pk": "SERVICES"}) == {"pk": {"S": "SERVICES"}}

    def test_int_field(self):
        assert _dynamo_typed({"v": 42}) == {"v": {"N": "42"}}

    def test_decimal_field(self):
        assert _dynamo_typed({"n": Decimal("7")}) == {"n": {"N": "7"}}

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            _dynamo_typed({"x": [1, 2, 3]})

    def test_bool_field(self):
        result = _dynamo_typed({"flag": True})
        assert result == {"flag": {"BOOL": True}}


# ---------------------------------------------------------------------------
# _handle_txn_cancel / _get_cancellation_reasons
# ---------------------------------------------------------------------------

class TestHandleTxnCancel:
    def test_event_ccf_returns_already_applied(self):
        exc = _make_txn_cancel(["ConditionalCheckFailed", "ConditionalCheckFailed"])
        result = _handle_txn_cancel(exc, SERVICE.id, 1)
        assert result == WriteOutcome.ALREADY_APPLIED

    def test_event_ccf_wins_regardless_of_item1(self):
        """Rule 1 always wins when item 2 is CCF, even if item 1 is also CCF."""
        exc = _make_txn_cancel(["ConditionalCheckFailed", "ConditionalCheckFailed"])
        assert _handle_txn_cancel(exc, SERVICE.id, 1) == WriteOutcome.ALREADY_APPLIED

    def test_record_ccf_only_returns_need_retry(self):
        from ghostpet.checker_writer import _NEED_RETRY
        exc = _make_txn_cancel(["ConditionalCheckFailed", "None"])
        assert _handle_txn_cancel(exc, SERVICE.id, 1) is _NEED_RETRY

    def test_transaction_conflict_returns_need_retry(self):
        from ghostpet.checker_writer import _NEED_RETRY
        exc = _make_txn_cancel(["TransactionConflict", "TransactionConflict"])
        assert _handle_txn_cancel(exc, SERVICE.id, 1) is _NEED_RETRY

    def test_throttling_returns_need_retry(self):
        from ghostpet.checker_writer import _NEED_RETRY
        exc = _make_txn_cancel(["ThrottlingError", "None"])
        assert _handle_txn_cancel(exc, SERVICE.id, 1) is _NEED_RETRY

    def test_validation_error_returns_dynamo_error(self):
        exc = _make_txn_cancel(["ValidationError", "None"])
        assert _handle_txn_cancel(exc, SERVICE.id, 1) == WriteOutcome.DYNAMO_ERROR

    def test_non_txn_cancel_exception_returns_dynamo_error(self):
        exc = _make_client_error("ProvisionedThroughputExceededException")
        assert _handle_txn_cancel(exc, SERVICE.id, 1) == WriteOutcome.DYNAMO_ERROR

    def test_get_cancellation_reasons_missing_response(self):
        assert _get_cancellation_reasons(Exception("boom")) == []

    def test_get_cancellation_reasons_parses_correctly(self):
        exc = _make_txn_cancel(["ConditionalCheckFailed", "None"])
        assert _get_cancellation_reasons(exc) == ["ConditionalCheckFailed", "None"]


# ---------------------------------------------------------------------------
# write_check_result — first write (no existing record)
# ---------------------------------------------------------------------------

class TestFirstWrite:
    def test_first_write_uses_transact_write_on_transition(self):
        """unknown + fast check → unknown→healthy transition → TransactWriteItems."""
        table = _make_table(get_item_return=None)
        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.WRITTEN
        table.meta.client.transact_write_items.assert_called_once()
        table.update_item.assert_not_called()

    def test_first_write_condition_is_attribute_not_exists(self):
        """The Update element inside TransactItems must use attribute_not_exists(pk)."""
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        items = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        condition = items[0]["Update"]["ConditionExpression"]
        assert condition == "attribute_not_exists(pk)"

    def test_first_write_version_is_1(self):
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        items = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        values = items[0]["Update"]["ExpressionAttributeValues"]
        assert values[":nextVersion"] == {"N": "1"}

    def test_first_write_status_becomes_healthy_after_fast_check(self):
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        items = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        values = items[0]["Update"]["ExpressionAttributeValues"]
        assert values[":status"] == {"S": "healthy"}

    def test_first_write_timeout_no_transition_uses_update_item(self):
        """unknown + timeout → no transition yet (need 3 failures) → UpdateItem."""
        table = _make_table(get_item_return=None)
        outcome = write_check_result(table, SERVICE, TIMEOUT_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.WRITTEN
        table.update_item.assert_called_once()
        table.meta.client.transact_write_items.assert_not_called()


# ---------------------------------------------------------------------------
# write_check_result — existing record, no transition
# ---------------------------------------------------------------------------

class TestUpdateNoTransition:
    def test_update_condition_uses_version_and_last_checked(self):
        table = _make_table(get_item_return=_stored_record(status="healthy", version=5))
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        kwargs = table.update_item.call_args.kwargs
        assert "#version = :expectedVersion AND lastChecked < :checkedAt" in kwargs["ConditionExpression"]

    def test_update_next_version_is_incremented(self):
        table = _make_table(get_item_return=_stored_record(version=10))
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
        assert values[":nextVersion"] == 11
        assert values[":expectedVersion"] == 10

    def test_checked_at_written(self):
        table = _make_table(get_item_return=_stored_record())
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
        assert values[":checkedAt"] == CHECKED_AT

    def test_response_ms_written(self):
        table = _make_table(get_item_return=_stored_record())
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        values = table.update_item.call_args.kwargs["ExpressionAttributeValues"]
        assert values[":rms"] == 150

    def test_key_uses_service_pk_and_sk(self):
        table = _make_table(get_item_return=_stored_record())
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        key = table.update_item.call_args.kwargs["Key"]
        assert key["pk"] == SERVICES_PK
        assert key["sk"] == service_sk(SERVICE.id)


# ---------------------------------------------------------------------------
# write_check_result — transition → TransactWriteItems
# ---------------------------------------------------------------------------

class TestWriteWithTransition:
    def test_transition_uses_transact_write(self):
        """unknown → healthy after one fast check → transition → TransactWriteItems."""
        table = _make_table(get_item_return=None)  # no existing record → unknown
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        # First check from unknown with fast → healthy (transition)
        table.meta.client.transact_write_items.assert_called_once()
        table.update_item.assert_not_called()

    def test_transact_items_has_update_and_put(self):
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        call_kwargs = table.meta.client.transact_write_items.call_args.kwargs
        items = call_kwargs["TransactItems"]
        assert len(items) == 2
        assert "Update" in items[0]
        assert "Put" in items[1]

    def test_event_put_has_attribute_not_exists_condition(self):
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        items = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        put = items[1]["Put"]
        assert put["ConditionExpression"] == "attribute_not_exists(pk)"

    def test_client_request_token_is_stable(self):
        from ghostpet.checker_items import build_idempotency_token
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        token_used = table.meta.client.transact_write_items.call_args.kwargs["ClientRequestToken"]
        expected = build_idempotency_token(SERVICE.id, CHECKED_AT)
        assert token_used == expected

    def test_event_pk_and_sk(self):
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        items = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        event = items[1]["Put"]["Item"]
        # Items are in DynamoDB typed format {"S": ...}
        assert event["pk"] == {"S": EVENTS_PK}
        expected_sk = event_sk(CHECKED_AT, SERVICE.id)
        assert event["sk"] == {"S": expected_sk}

    def test_event_contains_from_to_note(self):
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        items = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        event = items[1]["Put"]["Item"]
        assert event["from"] == {"S": "unknown"}
        assert event["to"] == {"S": "healthy"}
        assert event["note"]["S"] == "first fast check"

    def test_healthy_to_slow_transition(self):
        """Two consecutive slow checks produce a healthy→slow transition."""
        table = _make_table(get_item_return=_stored_record(
            status="healthy",
            consecutive_slow=1,  # one slow already counted
            last_checked="2026-11-15T01:58:00Z",
        ))
        write_check_result(table, SERVICE, SLOW_CHECK, CHECKED_AT, RETENTION)
        table.meta.client.transact_write_items.assert_called_once()
        items = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        event = items[1]["Put"]["Item"]
        assert event["from"] == {"S": "healthy"}
        assert event["to"] == {"S": "slow"}


# ---------------------------------------------------------------------------
# Stale check
# ---------------------------------------------------------------------------

class TestStaleCheck:
    def test_equal_checked_at_returns_stale(self):
        table = _make_table(get_item_return=_stored_record(last_checked=CHECKED_AT))
        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.STALE

    def test_newer_stored_returns_stale(self):
        table = _make_table(get_item_return=_stored_record(last_checked="2026-11-15T02:01:00Z"))
        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.STALE

    def test_stale_does_not_write(self):
        table = _make_table(get_item_return=_stored_record(last_checked=CHECKED_AT))
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        table.update_item.assert_not_called()
        table.meta.client.transact_write_items.assert_not_called()

    def test_older_stored_not_stale(self):
        table = _make_table(get_item_return=_stored_record(last_checked="2026-11-15T01:59:59Z"))
        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.WRITTEN


# ---------------------------------------------------------------------------
# Retry on version conflict (design 4.1 step 7)
# ---------------------------------------------------------------------------

class TestVersionConflictRetry:
    def _ccf_error(self):
        return _make_client_error("ConditionalCheckFailedException")

    def test_conflict_on_update_triggers_retry(self):
        """ConditionalCheckFailedException on UpdateItem causes a re-read and retry."""
        # First get_item: existing record.  Second get_item (after retry): same record.
        # First update_item: conflict.  Second: success.
        fresh_record = _stored_record(version=6, last_checked="2026-11-15T01:59:00Z")
        table = _make_table()
        table.get_item.side_effect = [
            {"Item": _stored_record(version=5, last_checked="2026-11-15T01:59:00Z")},
            {"Item": fresh_record},
        ]
        table.update_item.side_effect = [self._ccf_error(), None]

        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.WRITTEN
        assert table.get_item.call_count == 2
        assert table.update_item.call_count == 2

    def test_three_conflicts_returns_retries_exhausted(self):
        table = _make_table()
        table.get_item.return_value = {
            "Item": _stored_record(version=5, last_checked="2026-11-15T01:59:00Z")
        }
        table.update_item.side_effect = self._ccf_error()

        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.RETRIES_EXHAUSTED
        assert table.update_item.call_count == MAX_ATTEMPTS

    def test_retry_re_reads_record(self):
        """After a conflict the writer must re-read, not reuse the previous record."""
        table = _make_table()
        table.get_item.side_effect = [
            {"Item": _stored_record(version=5, last_checked="2026-11-15T01:59:00Z")},
            {"Item": _stored_record(version=6, last_checked="2026-11-15T01:59:00Z")},
            {"Item": _stored_record(version=7, last_checked="2026-11-15T01:59:00Z")},
        ]
        table.update_item.side_effect = [self._ccf_error(), self._ccf_error(), None]

        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.WRITTEN
        assert table.get_item.call_count == 3

    def test_stale_on_retry_returns_stale(self):
        """If the re-read finds a newer lastChecked, the check is dropped as stale."""
        # Second read has lastChecked == CHECKED_AT → stale
        table = _make_table()
        table.get_item.side_effect = [
            {"Item": _stored_record(version=5, last_checked="2026-11-15T01:59:00Z")},
            {"Item": _stored_record(version=6, last_checked=CHECKED_AT)},
        ]
        table.update_item.side_effect = [self._ccf_error()]

        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.STALE


# ---------------------------------------------------------------------------
# TransactWriteItems error handling
# ---------------------------------------------------------------------------

class TestTransactWriteErrors:
    def test_already_applied_when_event_exists(self):
        """Event sk already exists → ALREADY_APPLIED (design 4.3 rule 1)."""
        table = _make_table(get_item_return=None)
        # Both CCF: event exists AND record also fails
        table.meta.client.transact_write_items.side_effect = _make_txn_cancel(
            ["ConditionalCheckFailed", "ConditionalCheckFailed"]
        )
        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.ALREADY_APPLIED

    def test_record_ccf_triggers_retry(self):
        """Only record is CCF → version conflict → retry."""
        fresh = _stored_record(version=2, last_checked="2026-11-15T01:59:00Z")
        table = _make_table()
        table.get_item.side_effect = [
            {"Item": None},  # first read: no record → unknown
            {"Item": fresh},  # re-read after conflict
        ]
        table.meta.client.transact_write_items.side_effect = [
            _make_txn_cancel(["ConditionalCheckFailed", "None"]),
            None,
        ]
        # Second attempt: fast check from healthy (version 2 record) → no transition
        # but update_item is called this time
        table.update_item.return_value = {}

        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        # Either WRITTEN (via update or transact on retry) is acceptable.
        assert outcome in (WriteOutcome.WRITTEN, WriteOutcome.ALREADY_APPLIED)

    def test_transaction_conflict_counts_as_retry(self):
        """TransactionConflict → retry up to MAX_ATTEMPTS."""
        table = _make_table()
        table.get_item.return_value = {"Item": None}
        table.meta.client.transact_write_items.side_effect = _make_txn_cancel(
            ["TransactionConflict", "TransactionConflict"]
        )
        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.RETRIES_EXHAUSTED
        assert table.meta.client.transact_write_items.call_count == MAX_ATTEMPTS

    def test_validation_error_returns_dynamo_error(self):
        table = _make_table(get_item_return=None)
        table.meta.client.transact_write_items.side_effect = _make_txn_cancel(
            ["ValidationError", "None"]
        )
        outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.DYNAMO_ERROR

    def test_dynamo_error_does_not_retry(self):
        table = _make_table(get_item_return=None)
        table.meta.client.transact_write_items.side_effect = _make_txn_cancel(
            ["ValidationError", "None"]
        )
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        # Only one attempt — no retry on DYNAMO_ERROR
        assert table.meta.client.transact_write_items.call_count == 1


# ---------------------------------------------------------------------------
# Idempotency: same (service_id, checked_at) must produce the same token
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_client_request_token_is_deterministic(self):
        """Retrying with the same checked_at must reuse the same ClientRequestToken."""
        from ghostpet.checker_items import build_idempotency_token

        table = _make_table()
        # Simulate a transient conflict on first attempt, success on second
        table.get_item.side_effect = [
            {"Item": None},
            {"Item": None},
        ]
        table.meta.client.transact_write_items.side_effect = [
            _make_txn_cancel(["TransactionConflict", "TransactionConflict"]),
            None,
        ]

        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)

        calls = table.meta.client.transact_write_items.call_args_list
        tokens = [c.kwargs["ClientRequestToken"] for c in calls]
        expected = build_idempotency_token(SERVICE.id, CHECKED_AT)
        assert all(t == expected for t in tokens), (
            f"Tokens must be stable: {tokens}"
        )

    def test_different_checked_at_gives_different_token(self):
        from ghostpet.checker_items import build_idempotency_token
        t1 = build_idempotency_token(SERVICE.id, "2026-11-15T02:00:00Z")
        t2 = build_idempotency_token(SERVICE.id, "2026-11-15T02:01:00Z")
        assert t1 != t2


# ---------------------------------------------------------------------------
# get_item is called with ConsistentRead=True
# ---------------------------------------------------------------------------

class TestConsistentRead:
    def test_get_item_uses_consistent_read(self):
        table = _make_table(get_item_return=None)
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        kwargs = table.get_item.call_args.kwargs
        assert kwargs.get("ConsistentRead") is True

    def test_retry_get_item_also_uses_consistent_read(self):
        table = _make_table()
        table.get_item.side_effect = [
            {"Item": _stored_record(version=5, last_checked="2026-11-15T01:59:00Z")},
            {"Item": _stored_record(version=6, last_checked="2026-11-15T01:59:00Z")},
        ]
        table.update_item.side_effect = [_make_client_error("ConditionalCheckFailedException"), None]
        write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        for c in table.get_item.call_args_list:
            assert c.kwargs.get("ConsistentRead") is True


# ---------------------------------------------------------------------------
# Validation error path
# ---------------------------------------------------------------------------

class TestValidationError:
    def test_invalid_status_returns_validation_error(self):
        """If build_service_record raises InvalidStoredData, return VALIDATION_ERROR."""
        from ghostpet.model import InvalidStoredData
        table = _make_table(get_item_return=None)
        with patch("ghostpet.checker_writer.build_service_record",
                   side_effect=InvalidStoredData("site-1", "status", "bad")):
            outcome = write_check_result(table, SERVICE, FAST_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.VALIDATION_ERROR
        table.update_item.assert_not_called()
        table.meta.client.transact_write_items.assert_not_called()


# ---------------------------------------------------------------------------
# Down-state: failure stays in down (no transition)
# ---------------------------------------------------------------------------

class TestDownStaysDown:
    def test_failure_while_down_uses_update_not_transact(self):
        table = _make_table(get_item_return=_stored_record(
            status="down",
            consecutive_failures=3,
            last_checked="2026-11-15T01:59:00Z",
        ))
        outcome = write_check_result(table, SERVICE, TIMEOUT_CHECK, CHECKED_AT, RETENTION)
        assert outcome == WriteOutcome.WRITTEN
        table.update_item.assert_called_once()
        table.meta.client.transact_write_items.assert_not_called()


# ---------------------------------------------------------------------------
# Multiple status transitions in sequence
# ---------------------------------------------------------------------------

class TestMultipleTransitions:
    def _write(self, table, check, at):
        return write_check_result(table, SERVICE, check, at, RETENTION)

    def test_unknown_to_down_after_three_failures(self):
        """Three consecutive failures from unknown → down (with transition)."""
        table = _make_table(get_item_return=None)

        t1 = "2026-11-15T02:00:00Z"
        self._write(table, TIMEOUT_CHECK, t1)   # unknown, cf=1 → no transition
        # Reset mock and provide updated record for second check
        table.get_item.return_value = {"Item": _stored_record(
            status="unknown", consecutive_failures=1, version=1,
            last_checked=t1,
        )}
        table.meta.client.transact_write_items.reset_mock()
        table.update_item.reset_mock()

        t2 = "2026-11-15T02:01:00Z"
        self._write(table, TIMEOUT_CHECK, t2)
        table.get_item.return_value = {"Item": _stored_record(
            status="unknown", consecutive_failures=2, version=2,
            last_checked=t2,
        )}
        table.meta.client.transact_write_items.reset_mock()
        table.update_item.reset_mock()

        t3 = "2026-11-15T02:02:00Z"
        self._write(table, TIMEOUT_CHECK, t3)
        # Third failure from unknown → down → transition
        table.meta.client.transact_write_items.assert_called_once()
        event = table.meta.client.transact_write_items.call_args.kwargs["TransactItems"][1]["Put"]["Item"]
        assert event["from"] == {"S": "unknown"}
        assert event["to"] == {"S": "down"}
