import json

import pytest

from ghostpet.config import (
    MAX_SERVICES,
    ConfigError,
    Service,
    load_services,
    parse_services,
)


def entry(i=1, **overrides):
    item = {"id": f"site-{i}", "name": f"Example {i}", "url": "https://example.com/"}
    item.update(overrides)
    return item


def test_bundled_file_is_valid():
    services = load_services()
    assert services and all(isinstance(s, Service) for s in services)


def test_load_services_from_path(tmp_path):
    file = tmp_path / "services.json"
    file.write_text(json.dumps([entry(1), entry(2)]), encoding="utf-8")
    assert load_services(file) == [
        Service("site-1", "Example 1", "https://example.com/"),
        Service("site-2", "Example 2", "https://example.com/"),
    ]


def test_order_is_kept():
    assert [s.id for s in parse_services([entry(3), entry(1), entry(2)])] == ["site-3", "site-1", "site-2"]


def test_missing_or_unparseable_file(tmp_path):
    with pytest.raises(ConfigError):
        load_services(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("[{", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_services(bad)


@pytest.mark.parametrize("data", [[], {}, None, "site-1"])
def test_rejects_empty_or_non_list(data):
    with pytest.raises(ConfigError):
        parse_services(data)


def test_accepts_25_rejects_26_entries():
    assert len(parse_services([entry(i) for i in range(MAX_SERVICES)])) == 25
    with pytest.raises(ConfigError, match="maximum is 25"):
        parse_services([entry(i) for i in range(MAX_SERVICES + 1)])


def test_rejects_duplicate_id():
    with pytest.raises(ConfigError, match="duplicate"):
        parse_services([entry(1), entry(1, name="Other")])


@pytest.mark.parametrize("bad_id", ["site#1", "Site-1", "SITE", "-site", "", "a" * 64, "site 1", "site_1", 1, None])
def test_rejects_bad_id(bad_id):
    with pytest.raises(ConfigError, match="id must match"):
        parse_services([entry(id=bad_id)])


@pytest.mark.parametrize("bad_name", ["", "   ", "x" * 65, None, 5])
def test_rejects_missing_or_overlong_name(bad_name):
    with pytest.raises(ConfigError, match="name"):
        parse_services([entry(name=bad_name)])


def test_rejects_missing_name_and_url_keys():
    with pytest.raises(ConfigError):
        parse_services([{"id": "site-1", "url": "https://example.com/"}])
    with pytest.raises(ConfigError):
        parse_services([{"id": "site-1", "name": "Example"}])


@pytest.mark.parametrize(
    "bad_url",
    ["http://example.com/", "ftp://example.com/", "example.com", "https://", "https:///path",
     "https://example.com:99999/", "https://exa mple.com/", "", None],
)
def test_rejects_non_https_or_malformed_url(bad_url):
    with pytest.raises(ConfigError, match="url"):
        parse_services([entry(url=bad_url)])


@pytest.mark.parametrize(
    "bad_url",
    ["https://user@example.com/", "https://user:pass@example.com/", "https://:token@example.com/"],
)
def test_rejects_url_with_user_info(bad_url):
    with pytest.raises(ConfigError, match="user info"):
        parse_services([entry(url=bad_url)])


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://127.0.0.1/",
        "https://10.0.0.5/health",
        "https://192.168.1.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://8.8.8.8/",  # public IPs are rejected too: only reviewed DNS names
        "https://[::1]/",
        "https://[fe80::1]/",
        "https://[2001:db8::1]:8443/",
    ],
)
def test_rejects_ip_address_hosts(bad_url):
    with pytest.raises(ConfigError, match="not an IP address"):
        parse_services([entry(url=bad_url)])


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://0x7f.0.0.1/",
        "https://127.1/",
        "https://0177.0.0.1/",
        "https://example.0x1/",
    ],
)
def test_rejects_alternative_ipv4_forms(bad_url):
    with pytest.raises(ConfigError, match="not an IP address"):
        parse_services([entry(url=bad_url)])


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://localhost/",
        "https://LOCALHOST./",
        "https://localhost:8443/",
        "https://api.localhost/",
        "https://printer.local/",
        "https://db.internal/",
        "https://box.localdomain/",
        "https://router.home.arpa/",
        "https://1.0.0.127.in-addr.arpa/",
    ],
)
def test_rejects_localhost_and_internal_names(bad_url):
    with pytest.raises(ConfigError, match="localhost or an internal name"):
        parse_services([entry(url=bad_url)])


@pytest.mark.parametrize("bad_url", ["https://intranet/", "https://2130706433/", "https://0x7f000001/"])
def test_rejects_single_label_hosts(bad_url):
    with pytest.raises(ConfigError, match="fully qualified"):
        parse_services([entry(url=bad_url)])


@pytest.mark.parametrize("bad_url", ["https://exa_mple.com/", "https://-example.com/", "https://example-.com/"])
def test_rejects_invalid_host_characters(bad_url):
    with pytest.raises(ConfigError, match="invalid characters"):
        parse_services([entry(url=bad_url)])


@pytest.mark.parametrize(
    "good_url",
    [
        "https://example.com/",
        "https://Status.Example.COM/health",
        "https://example.com.:443/path?check=1",
        "https://api-1.example.co.uk/v2/health",
        "https://xn--bcher-kva.example/",
        "https://local.example.com/",
    ],
)
def test_accepts_public_dns_names(good_url):
    assert parse_services([entry(url=good_url)])[0].url == good_url


def test_rejects_unknown_keys():
    with pytest.raises(ConfigError, match="unknown keys"):
        parse_services([entry(token="secret")])


def test_error_messages_do_not_echo_urls():
    with pytest.raises(ConfigError) as exc:
        parse_services([entry(url="https://user:hunter2@example.com/")])
    assert "hunter2" not in str(exc.value)
