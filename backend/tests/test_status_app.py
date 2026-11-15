import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import status_app
from ghostpet.config import Service, load_services

NOW = datetime(2026, 11, 15, 2, 0, 0, tzinfo=UTC)
SERVICES = [Service("site-1", "Example", "https://example.com/"), Service("site-2", "Example Two", "https://example.com/docs")]
ERROR_BODY = {"error": {"code": "internal_error", "message": "Status is temporarily unavailable."}}

RECORD = {"id": "site-1", "status": "healthy", "responseMs": Decimal(84), "lastChecked": "2026-11-15T02:00:00Z"}
EVENT = {"serviceId": "site-1", "from": "unknown", "to": "healthy", "at": "2026-11-15T01:59:00Z",
         "note": "first fast check", "expiresAt": Decimal(1795312740)}


class FakeTable:
    """Records query() calls and returns canned pages per partition key. No boto3."""

    def __init__(self, service_pages=None, event_items=None, error=None):
        self.service_pages = list(service_pages if service_pages is not None else [{"Items": [RECORD]}])
        self.event_items = event_items if event_items is not None else [EVENT]
        self.error = error
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if kwargs["ExpressionAttributeValues"][":pk"] == "SERVICES":
            return self.service_pages.pop(0)
        return {"Items": self.event_items}

    def scan(self, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("Scan must not be used")


class FakeClientError(Exception):
    """Shaped like botocore.exceptions.ClientError."""

    def __init__(self, code, message):
        super().__init__(f"An error occurred ({code}): {message}")
        self.response = {"Error": {"Code": code, "Message": message},
                         "ResponseMetadata": {"RequestId": "REQ-123-SECRET-ID"}}


def call(table=None, services=SERVICES, config_error=None):
    table = table or FakeTable()
    response = status_app.handle(services=services, config_error=config_error,
                                 table_factory=lambda: table, now=lambda: NOW)
    return response, table


def log_lines(caplog):
    """Parsed JSON log lines. durationMs is checked for type, then removed so timing never flakes a test."""
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "status"]
    for line in lines:
        assert isinstance(line.pop("durationMs"), int)
    return lines


# --- success ------------------------------------------------------------------


def test_success_returns_200_json_with_cache_header():
    response, _ = call()
    assert response["statusCode"] == 200
    assert response["headers"] == {"Content-Type": "application/json", "Cache-Control": "public, max-age=5"}
    assert json.loads(response["body"]) == {
        "services": [
            {"id": "site-1", "name": "Example", "status": "healthy", "responseMs": 84,
             "lastChecked": "2026-11-15T02:00:00Z"},
            {"id": "site-2", "name": "Example Two", "status": "unknown", "responseMs": None, "lastChecked": None},
        ],
        "events": [{"serviceId": "site-1", "from": "unknown", "to": "healthy",
                    "at": "2026-11-15T01:59:00Z", "note": "first fast check"}],
    }


def test_empty_table_returns_all_unknown():
    response, _ = call(FakeTable(service_pages=[{"Items": []}], event_items=[]))
    body = json.loads(response["body"])
    assert [s["status"] for s in body["services"]] == ["unknown", "unknown"]
    assert body["events"] == []


def test_logs_one_json_line_on_success(caplog):
    caplog.set_level(logging.INFO, logger="status")
    call()
    lines = log_lines(caplog)
    assert len(lines) == 1
    assert lines[0]["event"] == "status_ok"
    assert (lines[0]["services"], lines[0]["events"]) == (2, 1)


# --- query arguments ----------------------------------------------------------


def test_service_query_uses_key_condition_projection_and_no_counters():
    _, table = call()
    service_call = table.calls[0]
    assert service_call["KeyConditionExpression"] == "pk = :pk AND begins_with(sk, :prefix)"
    assert service_call["ExpressionAttributeValues"] == {":pk": "SERVICES", ":prefix": "SERVICE#"}
    projection = service_call["ProjectionExpression"]
    assert "consecutive" not in projection and "version" not in projection
    assert service_call["ConsistentRead"] is False


def test_event_query_is_newest_first_limited_to_20():
    _, table = call()
    event_call = table.calls[1]
    assert event_call["ExpressionAttributeValues"] == {":pk": "EVENTS", ":prefix": "EVENT#"}
    assert event_call["ScanIndexForward"] is False
    assert event_call["Limit"] == 20
    assert "ExclusiveStartKey" not in event_call


def test_exactly_two_queries_and_no_scan():
    _, table = call()
    assert len(table.calls) == 2


def test_service_query_follows_pagination():
    second = {"id": "site-2", "status": "down", "responseMs": Decimal(5000), "lastChecked": "2026-11-15T02:00:00Z"}
    table = FakeTable(service_pages=[{"Items": [RECORD], "LastEvaluatedKey": {"pk": "SERVICES", "sk": "SERVICE#site-1"}},
                                     {"Items": [second]}])
    response, table = call(table)
    assert [s["status"] for s in json.loads(response["body"])["services"]] == ["healthy", "down"]
    assert table.calls[1]["ExclusiveStartKey"] == {"pk": "SERVICES", "sk": "SERVICE#site-1"}
    assert len(table.calls) == 3


# --- errors -------------------------------------------------------------------


def assert_error_response(response):
    assert response["statusCode"] == 500
    assert response["headers"] == {"Content-Type": "application/json", "Cache-Control": "no-store"}
    assert json.loads(response["body"]) == ERROR_BODY


def test_invalid_config_returns_500_and_logs_reason(caplog):
    caplog.set_level(logging.INFO, logger="status")
    response, table = call(services=None, config_error="entry 0: url must use https")
    assert_error_response(response)
    assert table.calls == []
    assert log_lines(caplog) == [{"event": "config_invalid", "reason": "entry 0: url must use https"}]


def test_empty_service_list_returns_500():
    response, _ = call(services=[])
    assert_error_response(response)


def test_invalid_stored_status_returns_500_and_logs_service_id(caplog):
    caplog.set_level(logging.INFO, logger="status")
    bad = dict(RECORD, id="site-2", status="Broken")
    response, _ = call(FakeTable(service_pages=[{"Items": [RECORD, bad]}]))
    assert_error_response(response)
    (line,) = log_lines(caplog)
    assert line["event"] == "invalid_stored_data"
    assert (line["serviceId"], line["field"]) == ("site-2", "status")


def test_invalid_stored_event_returns_500_and_logs_service_id(caplog):
    caplog.set_level(logging.INFO, logger="status")
    response, _ = call(FakeTable(event_items=[dict(EVENT, serviceId="site-9", at="2026-11-15 01:59")]))
    assert_error_response(response)
    (line,) = log_lines(caplog)
    assert (line["event"], line["serviceId"], line["field"]) == ("invalid_stored_data", "site-9", "at")


def test_dynamodb_error_returns_500_without_leaking_details(caplog):
    caplog.set_level(logging.INFO, logger="status")
    error = FakeClientError("ProvisionedThroughputExceededException", "arn:aws:dynamodb:us-east-1:123456789012:table/x")
    response, _ = call(FakeTable(error=error))
    assert_error_response(response)
    for text in ("123456789012", "arn:aws", "REQ-123", "Traceback", "ProvisionedThroughput"):
        assert text not in response["body"]
    (line,) = log_lines(caplog)
    assert line == {"event": "dynamodb_error", "errorCode": "ProvisionedThroughputExceededException"}
    assert "123456789012" not in json.dumps(line)


def test_table_factory_failure_returns_500(caplog):
    caplog.set_level(logging.INFO, logger="status")

    def broken_factory():
        raise KeyError("TABLE_NAME")

    response = status_app.handle(services=SERVICES, config_error=None, table_factory=broken_factory, now=lambda: NOW)
    assert_error_response(response)
    assert log_lines(caplog) == [{"event": "dynamodb_error", "errorCode": "KeyError"}]


def test_unexpected_shaping_error_returns_500(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="status")

    def explode(*args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(status_app, "build_status_response", explode)
    response, _ = call()
    assert_error_response(response)
    (line,) = log_lines(caplog)
    assert line["event"] == "internal_error" and line["errorType"] == "RuntimeError"
    assert "secret detail" not in json.dumps(line)


# --- module wiring ------------------------------------------------------------


def test_bundled_config_loads_at_import():
    assert status_app._CONFIG_ERROR is None
    assert status_app._SERVICES == load_services()


def test_lambda_entry_point_uses_module_config_and_table(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(status_app, "_get_table", lambda: table)
    response = status_app.handler({"rawPath": "/status"}, None)
    assert response["statusCode"] == 200
    ids = [s["id"] for s in json.loads(response["body"])["services"]]
    assert ids == [s.id for s in load_services()]


def test_lambda_entry_point_with_config_error(monkeypatch):
    monkeypatch.setattr(status_app, "_SERVICES", None)
    monkeypatch.setattr(status_app, "_CONFIG_ERROR", "bad config")
    monkeypatch.setattr(status_app, "_get_table", lambda: pytest.fail("table must not be used"))
    assert status_app.handler({}, None)["statusCode"] == 500
