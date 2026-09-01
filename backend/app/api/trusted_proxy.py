from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

    from backend.app.config import OpsPilotSettings

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


def _is_private_or_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return any(address in network for network in _PRIVATE_NETWORKS)


def client_ip(request: Request, settings: OpsPilotSettings | None = None) -> str:
    """Resolve client IP with optional trust of reverse-proxy forwarded headers.

    Forwarded headers are honored only when the direct peer is a private/loopback
    address (e.g. Caddy on the same VM) or when OPSPILOT_TRUST_PROXY_HEADERS=true.
    """
    peer = request.client.host if request.client is not None else ""
    trust_forwarded = settings is not None and settings.trust_proxy_headers
    if trust_forwarded or _is_private_or_loopback(peer):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    if peer:
        return peer
    return "unknown"
