"""
Process-local hostname aliases for constrained network paths.
"""

from __future__ import annotations

import os
import socket
from typing import Callable


_ORIGINAL_GETADDRINFO: Callable | None = None
DEFAULT_HOST_ALIASES: dict[str, str] = {
    "www.147soft.cn": "121.194.1.22",
}


def parse_host_aliases(value: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in value.replace(";", ",").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        host, ip_address = [part.strip() for part in item.split("=", 1)]
        if host and ip_address:
            aliases[host.lower()] = ip_address
    return aliases


def install_host_aliases(aliases: dict[str, str]) -> bool:
    global _ORIGINAL_GETADDRINFO
    if not aliases:
        return False
    if _ORIGINAL_GETADDRINFO is None:
        _ORIGINAL_GETADDRINFO = socket.getaddrinfo

    original = _ORIGINAL_GETADDRINFO

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        alias = aliases.get(str(host).lower())
        if alias:
            return original(alias, port, family, type, proto, flags)
        return original(host, port, family, type, proto, flags)

    socket.getaddrinfo = getaddrinfo
    return True


def install_host_aliases_from_env(env_name: str = "DAYDAYUP_HOST_ALIASES") -> bool:
    return install_host_aliases(parse_host_aliases(os.getenv(env_name, "")))


def install_host_aliases_with_defaults(
    env_name: str = "DAYDAYUP_HOST_ALIASES",
    default_aliases: dict[str, str] | None = None,
) -> bool:
    aliases = dict(default_aliases or {})
    aliases.update(parse_host_aliases(os.getenv(env_name, "")))
    return install_host_aliases(aliases)
