import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Probe:
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None


async def run_probe(
    client: httpx.AsyncClient,
    *,
    url: str,
    method: str,
    timeout: float,
    expected_status: int,
    keyword: str | None,
) -> Probe:
    """Одна проверка. Никогда не бросает исключений — любая ошибка это результат."""
    start = time.perf_counter()
    try:
        resp = await client.request(method, url, timeout=timeout, follow_redirects=False)
    except httpx.TimeoutException:
        return Probe(False, error=f"timeout after {timeout:g}s")
    except httpx.HTTPError as exc:
        return Probe(False, error=f"{type(exc).__name__}: {exc}"[:500])
    latency = (time.perf_counter() - start) * 1000

    if resp.status_code != expected_status:
        return Probe(
            False, resp.status_code, latency, f"expected {expected_status}, got {resp.status_code}"
        )
    if keyword and keyword not in resp.text:
        return Probe(False, resp.status_code, latency, f"keyword {keyword!r} not found")
    return Probe(True, resp.status_code, latency)
