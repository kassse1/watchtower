import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeTargetError(ValueError):
    pass


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    return addr.is_global


async def assert_public_url(url: str) -> None:
    """SSRF-защита: сервис, ходящий по пользовательским URL, не должен
    уметь стучаться в localhost, метаданные облака (169.254.169.254) и внутреннюю сеть."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeTargetError("only http(s) URLs are allowed")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, parts.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise UnsafeTargetError(f"cannot resolve host: {parts.hostname}") from exc
    for info in infos:
        if not _is_public(info[4][0]):
            raise UnsafeTargetError("target resolves to a non-public address")
