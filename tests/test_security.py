import pytest

from app.security import UnsafeTargetError, assert_public_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "ftp://example.com/",
        "file:///etc/passwd",
    ],
)
async def test_blocks_unsafe_targets(url):
    with pytest.raises(UnsafeTargetError):
        await assert_public_url(url)


async def test_allows_public_ip():
    await assert_public_url("http://8.8.8.8/")
