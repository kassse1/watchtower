import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # строка из панели Supabase/Render/Heroku вставляется как есть
        ("postgres://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        (
            "postgresql://u:p@host/db?sslmode=require",
            "postgresql+asyncpg://u:p@host/db?ssl=require",
        ),
        # пароль со спецсимволами не должен портиться
        ("postgresql://u:p%40ss@host/db", "postgresql+asyncpg://u:p%40ss@host/db"),
        # уже правильные адреса не трогаем
        ("postgresql+asyncpg://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("sqlite+aiosqlite:///./watchtower.db", "sqlite+aiosqlite:///./watchtower.db"),
    ],
)
def test_database_url_is_normalized(given, expected):
    assert Settings(_env_file=None, database_url=given).database_url == expected
