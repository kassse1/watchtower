import asyncio
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_BODY_BYTES = 1_000_000  # в теле ищем только ключевое слово; гигабайтный ответ нас не уронит


@dataclass(frozen=True)
class Target:
    url: str
    method: str = "GET"
    timeout: float = 10.0
    expected_status: int = 200
    keyword: str | None = None
    # Заранее проверенный IP. Если задан, соединяемся с ним, а не резолвим хост повторно.
    pinned_ip: str | None = None


@dataclass(frozen=True)
class Probe:
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    tls_expires_at: datetime | None = None


def _pinned_request_args(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """URL с IP вместо хоста + Host и SNI с исходным именем.

    TLS-сертификат при этом по-прежнему проверяется на исходное имя хоста.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    ip_host = f"[{ip}]" if ":" in ip else ip
    netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
    host_header = f"{host}:{parts.port}" if parts.port else host
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return urlunsplit(parts._replace(netloc=netloc)), {"Host": host_header}, extensions


def _tls_expiry(resp: httpx.Response) -> datetime | None:
    stream = resp.extensions.get("network_stream")
    ssl_obj = stream.get_extra_info("ssl_object") if stream else None
    cert = ssl_obj.getpeercert() if ssl_obj else None
    if not cert or "notAfter" not in cert:
        return None
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), UTC)


async def _read_capped(resp: httpx.Response) -> str:
    chunks, size = [], 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break
    return b"".join(chunks)[:MAX_BODY_BYTES].decode(resp.encoding or "utf-8", errors="replace")


async def _probe(client: httpx.AsyncClient, t: Target) -> Probe:
    url = t.url
    headers: dict[str, str] = {}
    extensions: dict[str, str] = {}
    if t.pinned_ip:
        url, headers, extensions = _pinned_request_args(t.url, t.pinned_ip)

    start = time.perf_counter()
    async with client.stream(
        t.method,
        url,
        headers=headers,
        extensions=extensions,
        timeout=t.timeout,
        follow_redirects=False,  # редирект мог бы увести на внутренний адрес
    ) as resp:
        latency = (time.perf_counter() - start) * 1000
        expiry = _tls_expiry(resp)
        if resp.status_code != t.expected_status:
            error = f"expected {t.expected_status}, got {resp.status_code}"
            return Probe(False, resp.status_code, latency, error, expiry)
        if t.keyword and t.keyword not in await _read_capped(resp):
            error = f"keyword {t.keyword!r} not found"
            return Probe(False, resp.status_code, latency, error, expiry)
        return Probe(True, resp.status_code, latency, None, expiry)


async def run_probe(client: httpx.AsyncClient, target: Target) -> Probe:
    """Одна проверка. Никогда не бросает исключений — любая ошибка это результат."""
    try:
        # Таймауты httpx действуют на каждую фазу отдельно; общий дедлайн нужен,
        # чтобы сервер, отдающий по байту в секунду, не держал проверку вечно.
        async with asyncio.timeout(target.timeout):
            return await _probe(client, target)
    except (TimeoutError, httpx.TimeoutException):
        return Probe(False, error=f"timeout after {target.timeout:g}s")
    except httpx.HTTPError as exc:
        return Probe(False, error=f"{type(exc).__name__}: {exc}"[:500])
