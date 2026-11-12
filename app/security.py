import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeTargetError(ValueError):
    pass


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped  # ::ffff:127.0.0.1 — это всё ещё localhost
    return addr.is_global


async def resolve_public_ip(url: str) -> str:
    """SSRF-защита. Резолвит хост и возвращает IP, к которому можно подключаться.

    Сервис, ходящий по пользовательским URL, не должен уметь стучаться в localhost,
    метаданные облака (169.254.169.254) и внутреннюю сеть. Отклоняем хост, если *хоть
    один* его адрес не публичный. Вызывающий обязан подключаться именно к возвращённому
    IP (см. checker.py): иначе между проверкой и запросом DNS может ответить иначе
    (DNS rebinding).
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeTargetError("only http(s) URLs are allowed")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, None, type=socket.SOCK_STREAM
        )
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeTargetError(f"cannot resolve host: {parts.hostname}") from exc
    ips = [str(info[4][0]) for info in infos]
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise UnsafeTargetError("target resolves to a non-public address")
    return ips[0]
