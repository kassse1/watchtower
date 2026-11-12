import httpx
import respx

from app.checker import run_probe

URL = "https://example.test/health"


async def probe(client, **kw):
    args = dict(url=URL, method="GET", timeout=2, expected_status=200, keyword=None) | kw
    return await run_probe(client, **args)


@respx.mock
async def test_ok():
    respx.get(URL).respond(200, text="hello")
    async with httpx.AsyncClient() as c:
        r = await probe(c)
    assert r.ok and r.status_code == 200 and r.latency_ms is not None


@respx.mock
async def test_unexpected_status():
    respx.get(URL).respond(503)
    async with httpx.AsyncClient() as c:
        r = await probe(c)
    assert not r.ok and "expected 200, got 503" in r.error


@respx.mock
async def test_keyword_missing_means_failure_even_with_200():
    respx.get(URL).respond(200, text="maintenance page")
    async with httpx.AsyncClient() as c:
        r = await probe(c, keyword="Welcome")
    assert not r.ok and "keyword" in r.error


@respx.mock
async def test_timeout_is_a_result_not_an_exception():
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as c:
        r = await probe(c)
    assert not r.ok and "timeout" in r.error


@respx.mock
async def test_connection_error():
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    async with httpx.AsyncClient() as c:
        r = await probe(c)
    assert not r.ok and "ConnectError" in r.error


@respx.mock
async def test_redirects_are_not_followed():
    respx.get(URL).respond(302, headers={"location": "http://169.254.169.254/"})
    async with httpx.AsyncClient() as c:
        r = await probe(c)
    assert not r.ok and r.status_code == 302
