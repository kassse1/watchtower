from playwright.async_api import expect

MONITOR = {"name": "Магазин", "url": "http://127.0.0.1:9/", "interval_seconds": 300}


def unexpected(page, allow=()):
    return [p for p in page.problems if not any(a in p for a in allow)]


async def test_add_edit_pause_delete(server, page):
    await page.goto(server.url)
    await expect(page.locator("#hero-title")).to_have_text("Добро пожаловать")

    # добавление: сначала ошибка валидации по-русски, затем успех
    await page.get_by_role("button", name="Добавить монитор").click()
    await page.fill("#f-name", "Блог")
    await page.fill("#f-url", "ftp://nope")
    await page.click("#add-submit")
    await expect(page.locator("#add-err")).to_contain_text("Некорректный адрес")
    await page.fill("#f-url", "http://127.0.0.1:9/blog")
    await page.click("#add-submit")
    card = page.locator(".mon")
    await expect(card).to_have_count(1)
    await expect(card.locator(".title b")).to_have_text("Блог")
    await expect(card.locator(".pill")).to_have_text("Ожидает проверки")

    # правка: имя меняется, адрес заблокирован
    await card.get_by_role("button", name="Изменить").click()
    await expect(page.locator("#form-title")).to_have_text("Изменить монитор")
    await expect(page.locator("#f-url")).to_be_disabled()
    await page.fill("#f-name", "Блог компании")
    await page.click("#add-submit")
    await expect(card.locator(".title b")).to_have_text("Блог компании")

    # пауза и фильтр
    await card.get_by_role("button", name="Поставить на паузу").click()
    await expect(card.locator(".pill")).to_have_text("На паузе")
    await page.locator('[data-filter="paused"]').click()
    await expect(card).to_have_count(1)
    await page.locator('[data-filter="down"]').click()
    await expect(card).to_have_count(0)
    await page.locator('[data-filter="all"]').click()

    # удаление требует подтверждения
    await card.get_by_role("button", name="Удалить").click()
    await expect(page.locator("#del-text")).to_contain_text("Блог компании")
    await page.click("#del-ok")
    await expect(card).to_have_count(0)

    with server.api() as api:
        assert api.get("/api/monitors").json() == []
    assert unexpected(page, allow=["422"]) == []  # 422 — ожидаемый ответ на неверный адрес


async def test_monitor_name_is_not_interpreted_as_html(server, page):
    """Название задаёт пользователь, а на странице статуса его видят все."""
    payload = '<img src=x onerror="window.pwned=1"><script>window.pwned=1</script>'
    with server.api() as api:
        assert api.post("/api/monitors", json={**MONITOR, "name": payload}).status_code == 201
    for path in ("/", "/status"):
        await page.goto(server.url + path)
        await expect(page.get_by_text(payload)).to_be_visible()  # показан как текст
        assert await page.evaluate("window.pwned") is None
    assert unexpected(page) == []


async def test_api_key_dialog_and_lockout(locked_server, page):
    await page.goto(locked_server.url)
    dialog = page.locator("#key-dlg")
    await expect(dialog).to_be_visible()

    await page.fill("#f-key", "wrong-key")
    await dialog.get_by_role("button", name="Войти").click()
    await expect(page.locator("#key-err")).to_have_text("Ключ не подошёл")

    await page.fill("#f-key", "e2e-secret")
    await dialog.get_by_role("button", name="Войти").click()
    await expect(dialog).to_be_hidden()
    await expect(page.locator("#hero-title")).to_have_text("Добро пожаловать")

    # ключ запомнен: после перезагрузки диалог не появляется
    await page.reload()
    await expect(page.locator("#hero-title")).to_have_text("Добро пожаловать")
    await expect(dialog).to_be_hidden()


async def test_status_page_is_public_and_hides_urls(locked_server, page):
    with locked_server.api() as api:
        secret = {**MONITOR, "url": "http://127.0.0.1:9/internal-admin"}
        assert api.post("/api/monitors", json=secret).status_code == 201
    await page.goto(locked_server.url + "/status")  # без ключа
    await expect(page.locator(".card")).to_have_count(1)
    await expect(page.locator(".card .row b")).to_have_text("Магазин")
    assert "internal-admin" not in await page.content()
    assert unexpected(page) == []
