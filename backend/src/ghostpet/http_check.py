"""HTTPS health-check implementation for the checker Lambda.

Performs a single HTTPS GET to a configured service URL and returns a
CheckResult via build_check_result().  Standard-library only: no extra
runtime dependencies.

Security contract (design.md 3, 4.1 step 1; security.md):
  - Only URLs from load_services() are ever requested (URL allowlist).
    perform_check() itself does not enforce this; checker_app.py must
    only pass URLs obtained from load_services().
  - The resolved IP is validated before connecting.  Loopback, RFC-1918,
    link-local, and AWS metadata addresses are refused (SSRF defence).
  - The same IP that passed validation is used for the actual TCP connection,
    so a second DNS lookup cannot swap in a different address (DNS-rebinding
    defence).
  - TLS certificate and hostname verification use Python's default context
    (ssl.create_default_context()), which validates the cert against the
    system trust store and enforces that the certificate matches the
    *original* hostname — not the raw IP.
  - HTTP redirects are not followed (security.md, design 4.1).
  - Response body is not read and not logged.
  - Caller receives a CheckResult via build_check_result(); no raw
    socket data escapes this module.
  - ProxyHandler({}) is passed explicitly to build_opener() so that
    HTTP_PROXY, HTTPS_PROXY, and NO_PROXY environment variables are
    completely ignored.  Without this, urllib would auto-add a ProxyHandler
    that reads those variables, allowing a proxy to intercept the pinned
    connection and bypass the SSRF defence.

Timeout design note:
  ``timeout_s`` (default 5.0 s) is passed as the socket timeout to
  ``opener.open()``, which propagates it to the underlying socket via
  ``sock.settimeout(timeout_s)``.  Each *individual* socket I/O phase
  (TCP connect, TLS handshake, response-header read) is therefore subject
  to a *per-phase* limit of ``timeout_s``, not an absolute wall-clock
  limit for the entire request.

  DNS resolution (``socket.getaddrinfo``) does **not** honour ``timeout_s``
  at all; it blocks for the duration of the OS-level resolver timeout
  (typically 5-30 s on most platforms).

  Practical worst-case ceiling per service check:
    DNS lookup      up to ~15 s   (OS resolver, not covered by timeout_s)
    TCP connect     <= 5 s        (timeout_s)
    TLS handshake   <= 5 s        (timeout_s)
    Response read   <= 5 s        (timeout_s)
    Total           ~30 s max

  The Lambda-level Timeout setting is the absolute hard ceiling.  It will
  be sized after DynamoDB I/O and multi-service timing is known (design.md
  section 10, known gaps).  A thread-based wall-clock guard can be added
  later if operational data shows it is necessary.
"""

from __future__ import annotations

import http.client as _hc
import ipaddress
import socket
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any

from ghostpet.checker_items import build_check_result
from ghostpet.decision import CheckResult

# Default values mirror the constants in petState.ts / decision.py.
DEFAULT_TIMEOUT_S: float = 5.0          # 5 seconds (design 4.1)
_TIMEOUT_MS: int = 5000                 # reported on timeout

# ---------------------------------------------------------------------------
# Blocked address categories (SSRF defence, design 4.1 step 1)
# ---------------------------------------------------------------------------

def _is_blocked_address(ip_str: str) -> tuple[bool, str]:
    """Return (blocked, reason) for a resolved IP string.

    Blocked categories:
      - loopback  (127.0.0.0/8, ::1)
      - private   (RFC 1918: 10/8, 172.16/12, 192.168/16; RFC 4193: fc00::/7)
      - link-local (169.254.0.0/16, fe80::/10) — includes AWS metadata endpoint
      - unspecified (0.0.0.0, ::)
      - multicast (224.0.0.0/4, ff00::/8)
      - reserved  (240.0.0.0/4 and other IANA-reserved blocks)

    The AWS metadata address 169.254.169.254 falls into link-local and is
    therefore blocked without a special-case rule.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, f"not a valid IP address: {ip_str!r}"

    if addr.is_loopback:
        return True, f"{ip_str} is a loopback address"
    # Check link-local before private: on Python 3.11+, 169.254.x.x satisfies
    # both is_link_local and is_private.  We check link-local first so the
    # reason string always mentions "link-local" for the AWS metadata address.
    if addr.is_link_local:
        return True, f"{ip_str} is a link-local address (includes AWS metadata)"
    if addr.is_private:
        return True, f"{ip_str} is a private address"
    if addr.is_unspecified:
        return True, f"{ip_str} is an unspecified address"
    if addr.is_multicast:
        return True, f"{ip_str} is a multicast address"
    if addr.is_reserved:
        return True, f"{ip_str} is a reserved address"
    return False, ""


# ---------------------------------------------------------------------------
# Custom HTTPS connection: pin to a pre-validated IP
# ---------------------------------------------------------------------------

class _PinnedHTTPSConnection(_hc.HTTPSConnection):
    """HTTPSConnection that connects to a caller-supplied IP.

    DNS-rebinding defence: the IP was resolved and validated *before* this
    connection is opened.  By overriding connect() we bypass the second
    name-to-IP resolution that http.client would otherwise perform, so a
    malicious DNS response after our check cannot redirect the connection.

    TLS is still negotiated with the *original hostname* as the SNI value
    and for certificate hostname verification, satisfying requirements 9.
    """

    _pinned_ip: str = ""          # set by _PinnedHTTPSHandler before open()

    def connect(self) -> None:
        # Guard against accidental direct instantiation without a pin.
        # An empty string would cause socket.create_connection(("", port))
        # to either raise an obscure OSError or, on some platforms, resolve
        # to localhost.  Raising ValueError here makes the misconfiguration
        # immediately visible instead of silently connecting to the wrong host.
        if not self._pinned_ip:
            raise ValueError(
                "_PinnedHTTPSConnection._pinned_ip must be set before connect(); "
                "use _PinnedHTTPSHandler to create this connection"
            )
        port = self.port or 443
        raw: socket.socket = socket.create_connection(
            (self._pinned_ip, port),
            timeout=self.timeout,
        )
        # server_hostname=self.host keeps SNI and cert CN/SAN validation
        # against the *original* hostname, not the IP (requirement 9).
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib handler that uses _PinnedHTTPSConnection."""

    def __init__(self, pinned_ip: str, context: ssl.SSLContext) -> None:
        # level=1 means "not verbose"; pass cadefault=False explicitly.
        super().__init__(context=context)
        self._pinned_ip = pinned_ip

    # urllib calls https_open → AbstractHTTPHandler.do_open(connection_class, req)
    def https_open(self, req: urllib.request.Request) -> Any:
        def connection_factory(host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
            conn = _PinnedHTTPSConnection(host, context=self._context, **kwargs)
            conn._pinned_ip = self._pinned_ip
            return conn

        return self.do_open(connection_factory, req)


# ---------------------------------------------------------------------------
# Redirect suppression
# ---------------------------------------------------------------------------

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block all HTTP redirects; treat them as a successful response.

    security.md and design 4.1 require that the checker does not follow
    redirects.  The redirect response itself (3xx) is returned to the
    caller as-is, which is then classified as a success by is_failure()
    (petState.ts test 16).
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        # Returning None suppresses the redirect; urllib raises HTTPError
        # with the 3xx code, which we catch and treat as a valid response.
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class BlockedAddressError(OSError):
    """Raised when DNS resolves to a blocked (private/loopback/…) IP."""


def perform_check(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> CheckResult:
    """Perform one HTTPS GET to *url* and return a CheckResult.

    Steps (design 4.1 step 1):
      1. Parse the URL and extract the hostname.
      2. Resolve the hostname to an IP via getaddrinfo.
      3. Validate the IP (loopback / private / link-local / reserved → block).
      4. Open an HTTPS connection to the validated IP, keeping the original
         hostname for SNI and certificate validation.
      5. Send a GET with redirects disabled.
      6. Return build_check_result() with the status code and elapsed time.

    On timeout  → CheckResult(statusCode=None, responseMs=5000, timedOut=True)
    On blocked IP or connection error
                → CheckResult(statusCode=None, responseMs=<elapsed>, timedOut=False)
    On 3xx      → CheckResult(statusCode=<3xx>, responseMs=<elapsed>, timedOut=False)
    On 4xx/5xx  → CheckResult(statusCode=<4xx/5xx>, responseMs=<elapsed>, timedOut=False)
    On TLS error→ CheckResult(statusCode=None, responseMs=<elapsed>, timedOut=False)

    Raises ValueError if *url* is not an https:// URL (caller should use
    load_services() which already enforces https).
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError(f"perform_check only accepts https:// URLs, got: {url!r}")

    hostname: str = parsed.hostname or ""
    port: int = parsed.port or 443

    # --- Step 1: DNS resolution + IP validation ---------------------------
    t_start = time.monotonic()
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        elapsed_ms = _elapsed_ms(t_start)
        # DNS failure counts as a connection error, not a timeout.
        return build_check_result(status_code=None, response_ms=elapsed_ms, timed_out=False)

    if not infos:
        elapsed_ms = _elapsed_ms(t_start)
        return build_check_result(status_code=None, response_ms=elapsed_ms, timed_out=False)

    # Prefer IPv4, keeping resolver order within each address family.
    # getaddrinfo returns (family, type, proto, canonname, sockaddr).
    # sockaddr is (ip, port) for IPv4 and (ip, port, flow, scope) for IPv6.
    selected = next((info for info in infos if info[0] == socket.AF_INET), infos[0])
    resolved_ip: str = selected[4][0]
    blocked, reason = _is_blocked_address(resolved_ip)
    if blocked:
        elapsed_ms = _elapsed_ms(t_start)
        raise BlockedAddressError(
            f"perform_check: DNS resolved {hostname!r} → {resolved_ip!r}: {reason}"
        )

    # --- Step 2: HTTPS GET with pinned IP ---------------------------------
    ssl_ctx = ssl.create_default_context()   # system trust store + hostname check

    opener = urllib.request.build_opener(
        # Explicitly suppress proxy env-var handling.  Without ProxyHandler({})
        # here, build_opener() auto-inserts ProxyHandler() which reads
        # HTTP_PROXY / HTTPS_PROXY / NO_PROXY and routes traffic through that
        # proxy — bypassing _PinnedHTTPSHandler and the SSRF defence entirely.
        urllib.request.ProxyHandler({}),
        _PinnedHTTPSHandler(resolved_ip, ssl_ctx),
        _NoRedirectHandler(),
    )

    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "ghost-pet-ops-checker/1.0"},
    )

    try:
        with opener.open(req, timeout=timeout_s) as resp:
            elapsed_ms = _elapsed_ms(t_start)
            status_code: int = resp.status
            # Do NOT read the response body (security.md: no logging of bodies).
            return build_check_result(
                status_code=status_code,
                response_ms=elapsed_ms,
                timed_out=False,
            )

    except urllib.error.HTTPError as exc:
        # HTTPError is raised for 3xx (redirect suppressed), 4xx, and 5xx.
        elapsed_ms = _elapsed_ms(t_start)
        return build_check_result(
            status_code=exc.code,
            response_ms=elapsed_ms,
            timed_out=False,
        )

    except TimeoutError:
        # socket.timeout is an alias for TimeoutError in Python 3.11+
        return build_check_result(
            status_code=None,
            response_ms=_TIMEOUT_MS,
            timed_out=True,
        )

    except OSError:
        # Covers ssl.SSLError, ConnectionRefusedError, etc.
        elapsed_ms = _elapsed_ms(t_start)
        return build_check_result(
            status_code=None,
            response_ms=elapsed_ms,
            timed_out=False,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _elapsed_ms(t_start: float) -> int:
    """Elapsed milliseconds since t_start, clamped to [0, _TIMEOUT_MS]."""
    ms = int((time.monotonic() - t_start) * 1000)
    return max(0, min(ms, _TIMEOUT_MS))


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "BlockedAddressError",
    "perform_check",
]
