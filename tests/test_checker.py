import asyncio
import ssl
from datetime import UTC, datetime

import httpx
import pytest
import respx
import trustme

from app.checker import MAX_BODY_BYTES, Target, _pinned_request_args, run_probe

URL = "https://example.test/health"


async def probe(**kw):
    async with httpx.AsyncClient() as client:
        return await run_probe(client, Target(**({"url": URL, "timeout": 2} | kw)))


@respx.mock
async def test_ok():
    respx.get(URL).respond(200, text="hello")
    r = await probe()
    assert r.ok and r.status_code == 200 and r.latency_ms is not None


@respx.mock
async def test_unexpected_status():
    respx.get(URL).respond(503)
    r = await probe()
    assert not r.ok and "expected 200, got 503" in r.error


@respx.mock
async def test_keyword_found():
    respx.get(URL).respond(200, text="<h1>Welcome</h1>")
    assert (await probe(keyword="Welcome")).ok


@respx.mock
async def test_keyword_missing_means_failure_even_with_200():
    respx.get(URL).respond(200, text="maintenance page")
    r = await probe(keyword="Welcome")
    assert not r.ok and "keyword" in r.error


@respx.mock
async def test_body_read_is_capped():
    respx.get(URL).respond(200, content=b"x" * (MAX_BODY_BYTES + 10) + b"NEEDLE")
    r = await probe(keyword="NEEDLE")
    assert not r.ok  # ключевое слово за пределами лимита не читаем


@respx.mock
async def test_timeout_is_a_result_not_an_exception():
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    r = await probe()
    assert not r.ok and "timeout" in r.error


@respx.mock
async def test_connection_error():
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    r = await probe()
    assert not r.ok and "ConnectError" in r.error


@respx.mock
async def test_redirects_are_not_followed():
    respx.get(URL).respond(302, headers={"location": "http://169.254.169.254/"})
    r = await probe()
    assert not r.ok and r.status_code == 302


def test_pinned_request_keeps_host_and_sni():
    url, headers, ext = _pinned_request_args("https://example.com:8443/a?b=1", "93.184.216.34")
    assert url == "https://93.184.216.34:8443/a?b=1"
    assert headers == {"Host": "example.com:8443"}
    assert ext == {"sni_hostname": "example.com"}

    url, headers, ext = _pinned_request_args("http://example.com/", "2606:2800::1")
    assert url == "http://[2606:2800::1]/" and headers == {"Host": "example.com"} and ext == {}


# --- Настоящие сокеты: то, что мок проверить не может -------------------------


class LocalServer:
    """Минимальный HTTP-сервер: запоминает заголовок Host и отвечает по сценарию."""

    def __init__(self, ssl_context=None, drip=False):
        self.ssl_context, self.drip, self.hosts = ssl_context, drip, []

    async def _handle(self, reader, writer):
        try:
            request = (await reader.readuntil(b"\r\n\r\n")).decode()
            self.hosts += [ln.split(": ")[1] for ln in request.split("\r\n") if ln[:5] == "Host:"]
            if self.drip:  # заголовки сразу, тело — по байту, бесконечно
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
                while True:
                    writer.write(b"x")
                    await writer.drain()
                    await asyncio.sleep(0.05)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass
        finally:
            writer.close()

    async def __aenter__(self):
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, ssl=self.ssl_context
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()


@pytest.fixture
def tls():
    ca = trustme.CA()
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("pinned.test").configure_cert(server_ctx)
    client_ctx = ssl.create_default_context()
    ca.configure_trust(client_ctx)
    return server_ctx, client_ctx


async def test_pinned_ip_over_real_tls(tls):
    """Имя pinned.test не существует в DNS. Запрос доходит только потому, что мы
    подключаемся к заранее проверенному IP, а сертификат при этом сверяется с именем."""
    server_ctx, client_ctx = tls
    async with LocalServer(server_ctx) as srv, httpx.AsyncClient(verify=client_ctx) as client:
        url = f"https://pinned.test:{srv.port}/"
        r = await run_probe(client, Target(url, keyword="ok", pinned_ip="127.0.0.1"))
        assert r.ok, r.error
        assert srv.hosts == [f"pinned.test:{srv.port}"]
        assert r.tls_expires_at is not None and r.tls_expires_at > datetime.now(UTC)


async def test_pinning_does_not_weaken_certificate_check(tls):
    server_ctx, client_ctx = tls
    async with LocalServer(server_ctx) as srv, httpx.AsyncClient(verify=client_ctx) as client:
        url = f"https://other-name.test:{srv.port}/"  # сертификат выписан на pinned.test
        r = await run_probe(client, Target(url, pinned_ip="127.0.0.1"))
        assert not r.ok and "CERTIFICATE_VERIFY_FAILED" in r.error


async def test_total_deadline_stops_slow_drip_body():
    """Сервер шлёт по байту каждые 50 мс: ни один таймаут фазы не срабатывает,
    спасает только общий дедлайн."""
    async with LocalServer(drip=True) as srv, httpx.AsyncClient() as client:
        target = Target(f"http://127.0.0.1:{srv.port}/", timeout=0.4, keyword="never")
        r = await asyncio.wait_for(run_probe(client, target), timeout=3)
        assert not r.ok and "timeout" in r.error
