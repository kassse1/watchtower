import asyncio
import socket

import pytest

from app.security import UnsafeTargetError, is_public_ip, resolve_public_ip


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",  # CGNAT
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4, замаскированный под IPv6
        "http://2130706433/",  # 127.0.0.1 в десятичной записи
        "ftp://example.com/",
        "file:///etc/passwd",
    ],
)
async def test_blocks_unsafe_targets(url):
    with pytest.raises(UnsafeTargetError):
        await resolve_public_ip(url)


async def test_allows_public_ip_and_returns_it_for_pinning():
    assert await resolve_public_ip("http://8.8.8.8/") == "8.8.8.8"


def test_ipv4_mapped_addresses_are_unwrapped():
    assert not is_public_ip("::ffff:10.0.0.1")
    assert is_public_ip("::ffff:8.8.8.8")


async def test_host_with_any_private_address_is_rejected(monkeypatch):
    """DNS отдаёт публичный и внутренний адрес сразу — классический обход фильтра."""

    async def fake_getaddrinfo(host, port, **kw):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeTargetError):
        await resolve_public_ip("https://sneaky.example/")
