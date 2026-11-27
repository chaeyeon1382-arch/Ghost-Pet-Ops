import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import checker_app
from ghostpet import checker_writer, http_check
from ghostpet.config import Service


SERVICES = [Service(name, name, f"https://{name}.example.com") for name in ("a", "b", "c")]
CHECKED_AT = "2026-11-27T00:00:00Z"
FAST_CHECK = {"statusCode": 200, "responseMs": 100, "timedOut": False}


@pytest.fixture
def scheduled_check(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "state-table")
    monkeypatch.setenv("EVENT_RETENTION_DAYS", "12")
    monkeypatch.setattr(checker_app, "load_services", lambda: SERVICES)
    monkeypatch.setattr(checker_app.time, "time", lambda: 0)
    check = Mock(return_value=FAST_CHECK)
    write = Mock(return_value=checker_writer.WriteOutcome.WRITTEN)
    dynamodb = Mock(return_value=(Mock(), Mock()))
    monkeypatch.setattr(checker_app, "perform_check", check)
    monkeypatch.setattr(checker_app, "write_check_result", write)
    monkeypatch.setattr(checker_app, "_get_dynamodb", dynamodb)
    context = Mock()
    context.get_remaining_time_in_millis.return_value = 120_000
    return context, check, write, dynamodb


def test_scheduler_empty_event_and_rotation(monkeypatch, scheduled_check):
    context, check, write, dynamodb = scheduled_check
    for minute in range(4):
        monkeypatch.setattr(checker_app.time, "time", lambda: minute * 60)
        checker_app.handler({}, context)
    expected = [SERVICES[(minute + delta) % 3] for minute in range(4) for delta in range(3)]
    assert [call.args[0] for call in check.call_args_list] == [s.url for s in expected]
    assert [call.args[1] for call in write.call_args_list] == expected
    assert all(call.args[4] == 12 for call in write.call_args_list)
    dynamodb.assert_called_with("state-table")


def test_service_exceptions_are_isolated(scheduled_check):
    context, check, write, _ = scheduled_check
    check.side_effect = [http_check.BlockedAddressError(), RuntimeError(), FAST_CHECK]
    write.side_effect = [OSError(), checker_writer.WriteOutcome.WRITTEN]
    checker_app.handler({"url": "https://unconfigured.example.com"}, context)
    assert [call.args[0] for call in check.call_args_list] == [s.url for s in SERVICES]
    assert [call.args[1] for call in write.call_args_list] == [SERVICES[0], SERVICES[2]]
    failed = write.call_args_list[0].args[2]
    assert failed["statusCode"] is None
    assert failed["timedOut"] is False


def test_low_time_skips_remaining_services_without_writes(scheduled_check):
    context, check, write, _ = scheduled_check
    context.get_remaining_time_in_millis.side_effect = [90_000, 60_000, 1_000]
    checker_app.handler({}, context)
    check.assert_called_once_with(SERVICES[0].url)
    write.assert_called_once()
    assert write.call_args.args[1] == SERVICES[0]


def test_sdk_uses_bounded_timeouts_and_no_sdk_retries(monkeypatch):
    import sys

    config = Mock()
    boto3 = Mock()
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore.config", SimpleNamespace(Config=config))
    table, client = checker_app._get_dynamodb.__wrapped__("state-table")
    options = config.call_args.kwargs
    assert options["connect_timeout"] == options["read_timeout"] == 1
    assert options["retries"]["total_max_attempts"] == 1
    boto3.resource.assert_called_once_with("dynamodb", config=config.return_value)
    boto3.client.assert_called_once_with("dynamodb", config=config.return_value)
    assert table is boto3.resource.return_value.Table.return_value
    assert client is boto3.client.return_value


def test_transaction_timeout_retries_identical_request(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(checker_writer.time, "sleep", sleep)
    table, client = Mock(), Mock()
    table.name = "state-table"
    table.get_item.return_value = {}
    transact = client.transact_write_items
    transact.side_effect = [TimeoutError(), TimeoutError(), None]
    outcome = checker_writer.write_check_result(
        table, SERVICES[0], FAST_CHECK, CHECKED_AT, 12, transaction_client=client
    )
    assert outcome is checker_writer.WriteOutcome.WRITTEN
    table.get_item.assert_called_once()
    assert transact.call_count == 3
    assert transact.call_args_list == [transact.call_args] * 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.1, 0.3]


@pytest.mark.parametrize("budgets", [[True, True, False], [True, True, True, False]])
def test_transaction_retry_stops_when_time_is_low(monkeypatch, budgets):
    monkeypatch.setattr(checker_writer.time, "sleep", Mock())
    table, client = Mock(), Mock()
    table.name = "state-table"
    table.get_item.return_value = {}
    client.transact_write_items.side_effect = TimeoutError()
    outcome = checker_writer.write_check_result(
        table, SERVICES[0], FAST_CHECK, CHECKED_AT, 12,
        transaction_client=client, can_attempt=Mock(side_effect=budgets),
    )
    assert outcome is checker_writer.WriteOutcome.TIME_BUDGET_EXHAUSTED
    client.transact_write_items.assert_called_once()


@pytest.mark.parametrize("ipv4, blocked", [("8.8.8.8", False), ("127.0.0.1", True)])
def test_http_prefers_ipv4_and_validates_the_selected_address(monkeypatch, ipv4, blocked):
    ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", 443, 0, 0))
    ipv4_info = (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ipv4, 443))
    monkeypatch.setattr(http_check.socket, "getaddrinfo", lambda *args, **kwargs: [ipv6, ipv4_info])
    opener = Mock(side_effect=RuntimeError("stop before network"))
    monkeypatch.setattr(http_check.urllib.request, "build_opener", opener)
    if blocked:
        with pytest.raises(http_check.BlockedAddressError):
            http_check.perform_check(SERVICES[0].url)
        opener.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match="stop before network"):
            http_check.perform_check(SERVICES[0].url)
        pinned = next(h for h in opener.call_args.args if isinstance(h, http_check._PinnedHTTPSHandler))
        assert pinned._pinned_ip == ipv4
