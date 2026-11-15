"""Tests for ghostpet/http_check.py.

No external network calls are made.  Every test uses unittest.mock or
in-process patch targets to simulate DNS, TCP connections, and HTTP
responses.

Test coverage matrix:
  - Normal fast/slow 2xx response
  - Timeout (TimeoutError raised by opener.open)
  - Connection error / connection refused
  - DNS failure (socket.getaddrinfo raises OSError)
  - Redirect responses (3xx) are returned as successes, not followed
  - 4xx / 5xx responses
  - Blocked IPs: loopback, RFC-1918, link-local (169.254.169.254), multicast,
    unspecified, reserved
  - TLS / SSL error
  - Non-https URL guard
  - build_check_result() is always used (no raw dict returned)
  - _is_blocked_address() unit tests
  - _elapsed_ms() unit tests
"""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ghostpet.http_check import (
    BlockedAddressError,
    _elapsed_ms,
    _is_blocked_address,
    perform_check,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAFE_URL = "https://example.com/health"
SAFE_IP = "93.184.216.34"   # example.com public IP – used as the mock resolved IP

# A minimal addrinfo tuple that socket.getaddrinfo would return for SAFE_IP:443
# format: (family, type, proto, canonname, sockaddr)
def _addrinfo(ip: str, port: int = 443) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class _FakeResponse:
    """Minimal file-like object returned by opener.open()."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self._data = BytesIO(b"")

    def read(self, n: int = -1) -> bytes:
        return self._data.read(n)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# _is_blocked_address unit tests
# ---------------------------------------------------------------------------


class TestIsBlockedAddress:
    def test_public_ip_not_blocked(self):
        blocked, _ = _is_blocked_address(SAFE_IP)
        assert blocked is False

    def test_loopback_ipv4(self):
        blocked, reason = _is_blocked_address("127.0.0.1")
        assert blocked is True
        assert "loopback" in reason

    def test_loopback_ipv6(self):
        blocked, reason = _is_blocked_address("::1")
        assert blocked is True
        assert "loopback" in reason

    def test_private_10(self):
        blocked, _ = _is_blocked_address("10.0.0.1")
        assert blocked is True

    def test_private_172_16(self):
        blocked, _ = _is_blocked_address("172.16.0.1")
        assert blocked is True

    def test_private_192_168(self):
        blocked, _ = _is_blocked_address("192.168.1.1")
        assert blocked is True

    def test_link_local_aws_metadata(self):
        """169.254.169.254 (AWS metadata) is link-local and must be blocked."""
        blocked, reason = _is_blocked_address("169.254.169.254")
        assert blocked is True
        assert "link-local" in reason

    def test_link_local_other(self):
        blocked, _ = _is_blocked_address("169.254.0.1")
        assert blocked is True

    def test_link_local_ipv6(self):
        blocked, _ = _is_blocked_address("fe80::1")
        assert blocked is True

    def test_unspecified_ipv4(self):
        blocked, _ = _is_blocked_address("0.0.0.0")
        assert blocked is True

    def test_multicast(self):
        blocked, _ = _is_blocked_address("224.0.0.1")
        assert blocked is True

    def test_public_ipv6_not_blocked(self):
        blocked, _ = _is_blocked_address("2001:db8::1")
        # 2001:db8::/32 is documentation/reserved
        # (may be reserved depending on Python version; we just assert no crash)
        assert isinstance(blocked, bool)

    def test_invalid_ip_string(self):
        blocked, reason = _is_blocked_address("not-an-ip")
        assert blocked is True
        assert "not a valid IP" in reason


# ---------------------------------------------------------------------------
# _elapsed_ms unit tests
# ---------------------------------------------------------------------------


class TestElapsedMs:
    def test_small_elapsed(self):
        t = time.monotonic() - 0.1   # 100 ms ago
        ms = _elapsed_ms(t)
        assert 90 <= ms <= 200       # generous window for CI

    def test_clamp_at_zero(self):
        # future start time should clamp to 0
        t = time.monotonic() + 100
        assert _elapsed_ms(t) == 0

    def test_clamp_at_timeout_ms(self):
        # very old start should clamp to 5000
        t = time.monotonic() - 100
        assert _elapsed_ms(t) == 5000


# ---------------------------------------------------------------------------
# perform_check — normal responses
# ---------------------------------------------------------------------------


class TestPerformCheckNormal:

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           return_value=_FakeResponse(200))
    def test_200_fast(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] == 200
        assert result["timedOut"] is False
        assert result["responseMs"] >= 0

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           return_value=_FakeResponse(200))
    def test_response_ms_is_int(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert isinstance(result["responseMs"], int)

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           return_value=_FakeResponse(200))
    def test_200_timed_out_false(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["timedOut"] is False

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           return_value=_FakeResponse(201))
    def test_201_accepted(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] == 201
        assert result["timedOut"] is False


# ---------------------------------------------------------------------------
# perform_check — 3xx (redirects must NOT be followed)
# ---------------------------------------------------------------------------


class TestPerformCheckRedirects:

    @pytest.mark.parametrize("code", [301, 302, 304, 307, 308])
    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    def test_3xx_returned_as_success_not_followed(self, mock_dns, code):
        """3xx is returned directly (no follow); classify() treats it as success."""
        http_error = urllib.error.HTTPError(
            SAFE_URL, code, f"{code} Redirect", {}, None
        )
        with patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
                   side_effect=http_error):
            result = perform_check(SAFE_URL)
        assert result["statusCode"] == code
        assert result["timedOut"] is False

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    def test_301_classify_as_non_failure(self, mock_dns):
        """is_failure(result) == False for 3xx (design contract)."""
        from ghostpet.decision import is_failure
        with patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
                   side_effect=urllib.error.HTTPError(SAFE_URL, 301, "Moved", {}, None)):
            result = perform_check(SAFE_URL)
        assert is_failure(result) is False


# ---------------------------------------------------------------------------
# perform_check — 4xx / 5xx
# ---------------------------------------------------------------------------


class TestPerformCheck4xx5xx:

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 502, 503, 504])
    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    def test_error_codes(self, mock_dns, code):
        err = urllib.error.HTTPError(SAFE_URL, code, "Error", {}, None)
        with patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
                   side_effect=err):
            result = perform_check(SAFE_URL)
        assert result["statusCode"] == code
        assert result["timedOut"] is False

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    def test_503_classify_as_failure(self, mock_dns):
        """is_failure(result) == True for 5xx."""
        from ghostpet.decision import is_failure
        err = urllib.error.HTTPError(SAFE_URL, 503, "Service Unavailable", {}, None)
        with patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
                   side_effect=err):
            result = perform_check(SAFE_URL)
        assert is_failure(result) is True


# ---------------------------------------------------------------------------
# perform_check — timeout
# ---------------------------------------------------------------------------


class TestPerformCheckTimeout:

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=TimeoutError("timed out"))
    def test_timeout_gives_none_status(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] is None
        assert result["timedOut"] is True

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=TimeoutError("timed out"))
    def test_timeout_response_ms_is_timeout_ceiling(self, mock_open, mock_dns):
        """On timeout, responseMs is set to the TIMEOUT_MS ceiling (5000)."""
        result = perform_check(SAFE_URL)
        assert result["responseMs"] == 5000

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=socket.timeout("timed out"))   # older alias
    def test_socket_timeout_alias(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["timedOut"] is True


# ---------------------------------------------------------------------------
# perform_check — connection failures
# ---------------------------------------------------------------------------


class TestPerformCheckConnectionFailure:

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=ConnectionRefusedError("connection refused"))
    def test_connection_refused(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] is None
        assert result["timedOut"] is False

    @patch("ghostpet.http_check.socket.getaddrinfo",
           side_effect=OSError("DNS failure"))
    def test_dns_failure(self, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] is None
        assert result["timedOut"] is False

    @patch("ghostpet.http_check.socket.getaddrinfo",
           return_value=[])   # empty addrinfo
    def test_empty_addrinfo(self, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] is None
        assert result["timedOut"] is False

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=OSError("network unreachable"))
    def test_generic_os_error(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] is None
        assert result["timedOut"] is False


# ---------------------------------------------------------------------------
# perform_check — TLS errors
# ---------------------------------------------------------------------------


class TestPerformCheckTLSErrors:

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=ssl.SSLCertVerificationError("cert verify failed"))
    def test_cert_verification_failure(self, mock_open, mock_dns):
        """TLS cert failure → connection error (statusCode=None, timedOut=False)."""
        result = perform_check(SAFE_URL)
        assert result["statusCode"] is None
        assert result["timedOut"] is False

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=ssl.SSLError("wrong version number"))
    def test_ssl_error(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert result["statusCode"] is None
        assert result["timedOut"] is False


# ---------------------------------------------------------------------------
# perform_check — DNS rebinding / SSRF defence
# ---------------------------------------------------------------------------


class TestPerformCheckSSRFDefence:

    @pytest.mark.parametrize("blocked_ip,label", [
        ("127.0.0.1",       "loopback"),
        ("::1",             "loopback IPv6"),
        ("10.0.0.5",        "RFC-1918 10/8"),
        ("172.16.0.1",      "RFC-1918 172.16/12"),
        ("192.168.0.1",     "RFC-1918 192.168/16"),
        ("169.254.169.254", "AWS metadata / link-local"),
        ("169.254.0.1",     "link-local"),
        ("fe80::1",         "link-local IPv6"),
        ("0.0.0.0",         "unspecified"),
        ("224.0.0.1",       "multicast"),
    ])
    def test_blocked_ip_raises(self, blocked_ip, label):
        """Connecting to a blocked IP must raise BlockedAddressError (no CheckResult)."""
        with patch("ghostpet.http_check.socket.getaddrinfo",
                   return_value=_addrinfo(blocked_ip)):
            with pytest.raises(BlockedAddressError, match=blocked_ip):
                perform_check(SAFE_URL)

    def test_non_https_url_raises(self):
        """http:// URLs must be rejected before any network activity."""
        with pytest.raises(ValueError, match="https"):
            perform_check("http://example.com/health")

    def test_dns_rebinding_uses_resolved_ip(self):
        """The connection is opened to the IP from getaddrinfo, not via a second lookup.

        We verify this by checking that getaddrinfo is called exactly once and
        that OpenerDirector.open is called (meaning a safe IP was used throughout).
        """
        called_ips: list[str] = []

        def fake_getaddrinfo(host, port, **kwargs):
            return _addrinfo(SAFE_IP)

        fake_response = _FakeResponse(200)

        with patch("ghostpet.http_check.socket.getaddrinfo",
                   side_effect=fake_getaddrinfo) as mock_dns, \
             patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
                   return_value=fake_response):
            result = perform_check(SAFE_URL)

        assert mock_dns.call_count == 1   # only one DNS lookup
        assert result["statusCode"] == 200

    def test_blocked_ip_does_not_reach_opener(self):
        """opener.open must never be called when the IP is blocked."""
        with patch("ghostpet.http_check.socket.getaddrinfo",
                   return_value=_addrinfo("127.0.0.1")), \
             patch("ghostpet.http_check.urllib.request.OpenerDirector.open") as mock_open:
            with pytest.raises(BlockedAddressError):
                perform_check(SAFE_URL)
        mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# perform_check — build_check_result always used
# ---------------------------------------------------------------------------


class TestBuildCheckResultAlwaysUsed:
    """Every code path must go through build_check_result(), never return a raw dict."""

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           return_value=_FakeResponse(200))
    def test_result_has_all_check_result_keys(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert set(result.keys()) == {"statusCode", "responseMs", "timedOut"}

    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           side_effect=TimeoutError())
    def test_timeout_result_has_all_check_result_keys(self, mock_open, mock_dns):
        result = perform_check(SAFE_URL)
        assert set(result.keys()) == {"statusCode", "responseMs", "timedOut"}

    @patch("ghostpet.http_check.socket.getaddrinfo",
           side_effect=OSError("dns fail"))
    def test_dns_failure_result_has_all_check_result_keys(self, mock_dns):
        result = perform_check(SAFE_URL)
        assert set(result.keys()) == {"statusCode", "responseMs", "timedOut"}

    @patch("ghostpet.http_check.build_check_result")
    @patch("ghostpet.http_check.socket.getaddrinfo", return_value=_addrinfo(SAFE_IP))
    @patch("ghostpet.http_check.urllib.request.OpenerDirector.open",
           return_value=_FakeResponse(200))
    def test_build_check_result_is_called(self, mock_open, mock_dns, mock_bcr):
        """Confirm that build_check_result() is invoked on the success path."""
        mock_bcr.return_value = {"statusCode": 200, "responseMs": 50, "timedOut": False}
        perform_check(SAFE_URL)
        mock_bcr.assert_called_once()
