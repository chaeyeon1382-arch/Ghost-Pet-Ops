"""Loads and validates the monitored service list (design.md section 3).

The list is reviewed in pull requests and bundled with the Lambda code. It is the only
source of URLs the checker may request (URL allowlist). It must not contain secrets.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SERVICES_FILE = Path(__file__).with_name("services.json")
MAX_SERVICES = 25
MAX_NAME_LENGTH = 64
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ALLOWED_KEYS = frozenset({"id", "name", "url"})
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_NUMERIC_LABEL_RE = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")
# Names that never point at a public site. "arpa" covers home.arpa and reverse-DNS names.
_BLOCKED_SUFFIXES = frozenset({"localhost", "local", "internal", "localdomain", "arpa"})


class ConfigError(ValueError):
    """The service list is missing or invalid."""


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    url: str


def load_services(path: str | Path | None = None) -> list[Service]:
    """Read and validate the service list. Raises ConfigError on any problem."""
    file = Path(path) if path is not None else SERVICES_FILE
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {file.name}: {type(exc).__name__}") from None
    return parse_services(data)


def parse_services(data: Any) -> list[Service]:
    """Validate already-parsed JSON. The whole list must be valid; there is no partial result."""
    if not isinstance(data, list) or not data:
        raise ConfigError("service list must be a non-empty JSON array")
    if len(data) > MAX_SERVICES:
        raise ConfigError(f"service list has {len(data)} entries; the maximum is {MAX_SERVICES}")

    services: list[Service] = []
    seen: set[str] = set()
    for index, entry in enumerate(data):
        where = f"entry {index}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: must be an object")
        unknown = set(entry) - _ALLOWED_KEYS
        if unknown:
            raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")

        service_id = entry.get("id")
        if not isinstance(service_id, str) or not _ID_RE.match(service_id):
            raise ConfigError(f"{where}: id must match {_ID_RE.pattern}")
        if service_id in seen:
            raise ConfigError(f"{where}: duplicate id {service_id!r}")
        seen.add(service_id)

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH:
            raise ConfigError(f"{where} ({service_id}): name must be 1 to {MAX_NAME_LENGTH} characters")

        url = entry.get("url")
        _check_url(url, f"{where} ({service_id})")
        services.append(Service(id=service_id, name=name, url=url))
    return services


def _check_url(url: Any, where: str) -> None:
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        raise ConfigError(f"{where}: url must be a non-empty string without whitespace")
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on an invalid port
    except ValueError:
        raise ConfigError(f"{where}: url is not valid") from None
    if parts.scheme != "https":
        raise ConfigError(f"{where}: url must use https")
    if not parts.hostname:
        raise ConfigError(f"{where}: url must have a host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ConfigError(f"{where}: url must not contain user info")
    _check_public_hostname(parts.hostname, where)


def _check_public_hostname(hostname: str, where: str) -> None:
    """Allow only public DNS names. Blocks IP literals, localhost and internal names (SSRF).

    This is a static check of the configured text. A public name can still resolve to a
    private address, so the checker must also verify the resolved IP at request time.
    """
    host = hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ConfigError(f"{where}: url host must be a DNS name, not an IP address")

    labels = host.split(".")
    if labels[-1] in _BLOCKED_SUFFIXES or ".".join(labels[-2:]) in _BLOCKED_SUFFIXES:
        raise ConfigError(f"{where}: url host must not be localhost or an internal name")
    if len(labels) < 2:
        raise ConfigError(f"{where}: url host must be a fully qualified public DNS name")
    if not all(_LABEL_RE.match(label) for label in labels):
        raise ConfigError(f"{where}: url host has invalid characters")
    # Numeric or hex last labels (127.1, 0x7f.0.0.1) are IPv4 forms that resolvers accept. No real TLD looks like that.
    if _NUMERIC_LABEL_RE.match(labels[-1]):
        raise ConfigError(f"{where}: url host must be a DNS name, not an IP address")
