"""Opt-in real Edge/Playwright acceptance against the local running project.

Use an isolated Playwright installation; no browser/runtime installation is
required by the application. All writes belong to this script's disposable user.
Run from the repository root with `python apps/api/tests/quality/search_audit_browser.py`.
"""

import asyncio
import json
import re
import secrets
import sys
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright, expect
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.config import Settings  # noqa: E402
from app.database.connection import create_database  # noqa: E402

BASE = "http://127.0.0.1:3000"
POST = "**/api/projects/*/requests"
RECEIPT = "**/api/projects/*/requests/*"
CHAT = "**/api/chats"
LEDGER = ROOT / ".local/search-audit-browser-ledger.json"
RESULT = ROOT / ".local/search-audit-browser.json"


async def database(callback, *, write=False):
    engine, sessions = create_database(Settings().database_url)
    try:
        async with sessions.begin() as db:
            if not write:
                await db.execute(text("SET TRANSACTION READ ONLY"))
            return await callback(db)
    finally:
        await engine.dispose()


async def cleanup(state):
    if not state.get("user"):
        return

    async def delete(db):
        user = state["user"]
        assert state["name"] == "search-audit-browser-" + state["run"]
        assert await db.scalar(text("SELECT display_name FROM users WHERE id=:id"), {"id": user}) == state["name"]
        projects = list(await db.scalars(text("SELECT id FROM projects WHERE user_id=:id"), {"id": user}))
        chats = list(await db.scalars(text("SELECT id FROM chats WHERE user_id=:id"), {"id": user}))
        state["projects"], state["chats"] = projects, chats
        LEDGER.write_text(json.dumps(state), encoding="utf8")
        for table, ids in (("projects", projects), ("chats", chats)):
            for identifier in ids:
                await db.execute(text(f"DELETE FROM {table} WHERE id=:id AND user_id=:owner"),
                                 {"id": identifier, "owner": user})
        await db.execute(text("DELETE FROM users WHERE id=:id AND display_name=:name"),
                         {"id": user, "name": state["name"]})
        return {"projects": len(projects), "chats": len(chats), "users": 1}

    result = await database(delete, write=True)
    LEDGER.unlink()
    return result


async def read(page, path):
    r = await page.request.get(BASE + "/api" + path, timeout=120000)
    assert r.ok, (path, r.status)
    return await r.json()


async def composer(page):
    field = page.locator(".research-composer textarea:visible")
    if not await field.count():
        opener = page.get_by_role("button", name="Открыть чат")
        if await opener.count():
            await opener.click()
    await expect(field).to_be_editable(timeout=120000)
    return field


async def idle(page):
    await expect(page.locator(".research-composer button:visible")).to_have_attribute("aria-busy", "false", timeout=120000)


async def project(page):
    rows = await read(page, "/projects")
    chat = await page.evaluate("localStorage.getItem('forma-chat-id')")
    return next(p for p in rows if p["chat_id"] == chat)


async def active(page):
    p = await project(page)
    return await read(page, "/researches/chat/" + p["chat_id"])


async def all_products(page, research):
    products, cursor = list(research["products"]), research["next_cursor"]
    while cursor:
        r = await page.request.get(BASE + f"/api/researches/{research['id']}/products",
                                   params={"cursor": cursor, "limit": 100}, timeout=120000)
        assert r.ok, r.status
        value = await r.json()
        products.extend(value["products"])
        cursor = value["next_cursor"]
    assert len(products) == len({p["id"] for p in products}) == research["matching_count"]
    return products


async def grid(page, research, *, full=False):
    await expect(page.locator(".research-heading h1")).to_have_text(research["effective_title"], timeout=120000)
    await expect(page.locator(".research-stats span").first).to_contain_text(str(research["matching_count"]))
    cards = page.locator(".research-main .product-card[data-offer-id]")
    products = await all_products(page, research) if full else research["products"]
    ids = [p["id"] for p in products]
    if full:
        while await cards.count() < len(ids):
            prior = await cards.count()
            await page.get_by_role("button", name=re.compile("Показать ещё 100")).click()
            await page.wait_for_function("n=>document.querySelectorAll('.research-main .product-card[data-offer-id]').length>n", arg=prior, timeout=120000)
    await page.wait_for_function("ids=>JSON.stringify([...document.querySelectorAll('.research-main .product-card[data-offer-id]')].map(e=>e.dataset.offerId))===JSON.stringify(ids)", arg=ids, timeout=120000)
    if research["view"]["sorting"] in ("price_asc", "price_desc"):
        desc = research["view"]["sorting"] == "price_desc"
        assert products == sorted(products, key=lambda p: (p["price_kzt"] is None, (p["price_kzt"] or 0)*(-1 if desc else 1), p["id"]))
        for row, product in zip(await cards.locator(".product-meta strong").all_text_contents(), products, strict=True):
            if product["price_kzt"] is not None:
                assert int(re.sub(r"\D", "", row)) == product["price_kzt"]
    return ids


async def send(page, query, *, status=202, full=False):
    field = await composer(page)
    await field.fill(query)
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/requests"), timeout=120000) as pending:
        await field.press("Enter")
    response = await pending.value
    assert response.status == status, (query, response.status)
    result = await response.json()
    await idle(page)
    await expect(await composer(page)).to_have_value("")
    if status == 202:
        r = await active(page)
        assert r["id"] == result["research"]["id"]
        return r, await grid(page, r, full=full), result["turn"]["request_id"]
    return result


async def choose(page, attribute, value):
    field = page.locator(f'[data-attribute-id="{attribute}"]')
    await field.locator(".ui-select-trigger").click()
    async with page.expect_response(lambda r: r.request.method == "PATCH" and r.url.endswith("/view"), timeout=120000) as pending:
        await page.get_by_role("option", name=re.compile(rf"^{re.escape(value)}(?:\s|$)")).click()
    assert (await pending.value).ok
    await expect(page.locator(".research-hud-status")).not_to_contain_text("Обновляю результаты…", timeout=120000)
    r = await active(page)
    return r, await grid(page, r, full=True)


async def history_count(page, key):
    p = await project(page)
    value = await read(page, f"/chats/{p['chat_id']}/history?limit=100")
    return sum(m["role"] == "user" and m["request_id"] == key for m in value["messages"])


async def failed_send(page, query, handler, pattern=POST):
    await page.route(pattern, handler)
    field = await composer(page)
    await field.fill(query)
    await field.press("Enter")
    await expect(page.get_by_text("Отправка не подтверждена. Текст остался в поле для повтора.")).to_be_visible(timeout=120000)
    await idle(page)
    await expect(await composer(page)).to_have_value(query)
    await page.unroute(pattern, handler)


async def main():
    assert not LEDGER.exists(), "Finish exact-ID cleanup of the previous browser run first"
    state = {"run": uuid4().hex}
    state["name"] = "search-audit-browser-" + state["run"]
    password = secrets.token_urlsafe(32)
    evidence = {"checks": {}}

    def passed(label, **values):
        evidence["checks"][label] = values or True
        RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8")
        print(label, flush=True)

    async def current_stock(db):
        return await db.scalar(text("SELECT stock FROM product_observations WHERE offer_id='vto:1823107'"))

    stock = await database(current_stock)
    assert stock and stock > 0
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel="msedge", headless=True)
            context = await browser.new_context(viewport={"width": 1680, "height": 1050})
            # Search assertions do not require hundreds of remote product photos.
            async def images(route):
                if route.request.resource_type == "image":
                    await route.abort()
                else:
                    await route.continue_()
            await context.route("**/*", images)
            page = await context.new_page()
            await page.goto(BASE, wait_until="domcontentloaded")
            await page.get_by_role("button", name="Нет аккаунта? Создать").click()
            await page.locator('input[name="name"]').fill(state["name"])
            await page.locator('input[name="password"]').fill(password)
            await page.locator('input[name="repeat"]').fill(password)
            async with page.expect_response(lambda r: r.url.endswith("/api/auth/register"), timeout=120000) as pending:
                await page.get_by_role("button", name="Создать аккаунт", exact=True).click()
            registered = await pending.value
            assert registered.ok
            state["user"] = (await registered.json())["id"]
            LEDGER.write_text(json.dumps(state), encoding="utf8")
            await composer(page)
            creation = {}

            async def lost_create(route):
                creation["key"] = route.request.post_data_json["request_id"]
                response = await route.fetch(timeout=120000)
                assert response.ok
                creation["chat"] = (await response.json())["id"]
                await route.abort("failed")

            await failed_send(page, "powerbank", lost_create, CHAT)
            await page.reload(wait_until="domcontentloaded")
            await expect(await composer(page)).to_have_value("powerbank")
            first, _, key = await send(page, "powerbank")
            assert len(await read(page, "/projects")) == 1 and first["chat_id"] == creation["chat"]
            assert await history_count(page, key) == 1
            passed("first_request_lost_chat_creation_F5", count=first["matching_count"])

            await send(page, "Ежедневники")
            diary, manual = await choose(page, "paper_format", "A5")
            assert manual
            _, asc, _ = await send(page, "Бутылки 750 мл, сначала самые дешёвые", full=True)
            await expect(page.get_by_text("Сначала дешёвые", exact=True)).to_be_visible()
            await expect(page.locator('[data-attribute-id="capacity_ml"]')).to_contain_text("750")
            assert not await page.locator('[data-attribute-id="paper_format"]').count()
            _, comparable, _ = await send(page, "Бутылки 750 мл", full=True)
            assert set(asc) == set(comparable)
            _, descending, _ = await send(page, "Сначала самые дорогие", full=True)
            assert set(descending) == set(asc)
            passed("new_category_manual_format_and_DOM_price_order", bottles=len(asc), manual_a5=diary["matching_count"])
            for spelling in ("А5", "A5"):
                _, ids, _ = await send(page, "Ежедневники " + spelling, full=True)
                assert set(ids) == set(manual)
                await expect(page.locator('[data-attribute-id="paper_format"]')).to_contain_text("Из запроса: A5")
            assert len(manual) > 100
            selected = ids[100]
            await page.locator(f'.product-card[data-offer-id="{selected}"] input[type="checkbox"]').check()
            await expect(page.get_by_label("Выбрано товаров: 1")).to_be_visible(timeout=120000)
            p = await project(page)
            assert (await read(page, f"/projects/{p['id']}/collection"))["selected_ids"] == [selected]
            await send(page, "Ручки Tippo от 8 000 шт до 300 тенге")
            await expect(page.locator('.effective-filter-control input').last).to_have_value("8000")
            _, current_desc, _ = await send(page, "Бутылки 750 мл, сначала самые дорогие", full=True)
            assert current_desc == descending
            await page.reload(wait_until="domcontentloaded")
            await grid(page, await active(page))
            await expect(page.get_by_label("Выбрано товаров: 1")).to_be_visible(timeout=120000)
            assert (await read(page, f"/projects/{p['id']}/collection"))["selected_ids"] == [selected]
            passed("A5_both_alphabets_selection_from_second_page_F5_and_Tippo", selected_id=selected, format_count=len(manual))

            mixed = "Ручки синие и термокружки красные"
            await send(page, mixed)
            async with page.expect_response(lambda r: r.request.method == "PATCH" and r.url.endswith("/view"), timeout=120000) as pending:
                await page.get_by_role("button", name="Скрыть категорию «Ручки»").click()
            assert (await pending.value).ok
            hidden = await active(page)
            ids = await grid(page, hidden, full=True)
            for command in ("Оставь только термокружки", "Покажи только термокружки из текущей выдачи"):
                await send(page, mixed)
                _, narrowed, _ = await send(page, command, full=True)
                assert set(narrowed) == set(ids)
                await expect(page.locator(".query-category-chip.is-hidden")).to_contain_text("Ручки")
            passed("contextual_narrowing_matches_hide_button", count=len(ids))

            await send(page, "Бутылки по 500 мл")
            await expect(page.locator('[data-attribute-id="capacity_ml"]')).to_contain_text("500")
            r, sku, _ = await send(page, f"GF-12449.30 от {stock} шт", full=True)
            assert sku == ["vto:1823107"]
            await expect(page.get_by_text(re.compile("Артикул: GF-12449.30"))).to_be_visible()
            await send(page, "GF-12449.30")
            _, refined, _ = await send(page, f"Теперь от {stock} шт", full=True)
            assert refined == sku
            _, above, _ = await send(page, f"GF-12449.30 от {stock+1} шт", full=True)
            assert not above
            passed("SKU_quantity_bound_and_volume_HUD", stock=stock)

            old, _, _ = await send(page, "Бутылки 750 мл")
            await send(page, "Ручки от 7000 до 500 тенге", status=422)
            await expect(page.get_by_text(re.compile("Предыдущая выдача — уточните диапазон цены"))).to_be_visible(timeout=120000)
            await expect(page.locator(".research-messages")).to_contain_text("Минимальная цена выше максимальной. Какой диапазон использовать?")
            assert (await active(page))["id"] == old["id"]
            corrected, _, _ = await send(page, "От 100 до 500 тенге")
            assert corrected["intent"]["branches"][0]["category"] == "pen"
            await send(page, "Красные ручки без бордовых")
            await expect(page.get_by_text(re.compile("Без бордового оттенка"))).to_be_visible()
            await send(page, "Внешние аккумуляторы, шопперы, термокружки и карандаши. Цвет navy blue. Тираж 150 шт.")
            await expect(page.get_by_role("combobox", name=re.compile("Цвет.*Navy"))).to_be_visible()
            passed("price_clarification_and_negative_shade")

            old = await active(page)
            rejected = {}
            async def abort(route):
                rejected["key"] = route.request.post_data_json["request_id"]
                await route.abort("failed")
            await failed_send(page, "Бутылки 750 мл", abort)
            assert await history_count(page, rejected["key"]) == 0 and (await active(page))["id"] == old["id"]
            await expect(page.locator(".research-error")).to_contain_text("Проверьте подключение")
            await page.reload(wait_until="domcontentloaded")
            await expect(await composer(page)).to_have_value("Бутылки 750 мл")
            _, _, retried = await send(page, "Бутылки 750 мл")
            assert retried == rejected["key"] and await history_count(page, retried) == 1
            passed("network_failure_before_acceptance_same_ID_retry_F5")

            async def unavailable(route):
                await route.fulfill(status=503, content_type="application/json", body='{"detail":"Service unavailable"}')
            await failed_send(page, "Рюкзаки", unavailable)
            await expect(page.locator(".research-error")).to_contain_text("Сервис временно недоступен")
            await expect(page.locator(".research-error")).not_to_contain_text("API_BASE_URL")
            await send(page, "Рюкзаки")
            passed("generic_503_not_configuration")

            # F5 during a transport outage does not mean the session expired.
            async def offline_me(route):
                await route.abort("failed")
            old = await active(page)
            await page.route("**/api/auth/me", offline_me)
            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator('.auth-card [role="alert"]')).to_contain_text("Проверьте подключение", timeout=120000)
            assert not await page.locator('input[name="password"]').count()
            await page.unroute("**/api/auth/me", offline_me)
            await page.get_by_role("button", name="Повторить подключение").click()
            await grid(page, old)
            await expect(page.get_by_label("Выбрано товаров: 1")).to_be_visible(timeout=120000)
            passed("F5_network_failure_is_not_logout")

            committed = {}
            async def block_receipt(route):
                await route.abort("failed")
            async def lost_response(route):
                response = await route.fetch(timeout=120000)
                assert response.ok
                committed.update(await response.json())
                await route.abort("failed")
            await page.route(RECEIPT, block_receipt)
            # History may arrive before the failed receipt. The composer must
            # still retain the draft until an authoritative acknowledgement.
            await page.route(POST, lost_response)
            field = await composer(page)
            await field.fill("Термокружки по 500 мл")
            await field.press("Enter")
            await idle(page)
            await expect(await composer(page)).to_have_value("Термокружки по 500 мл")
            saved = committed["turn"]["request_id"]
            assert await history_count(page, saved) == 1
            await page.unroute(POST, lost_response)
            await page.reload(wait_until="domcontentloaded")
            await expect(await composer(page)).to_have_value("Термокружки по 500 мл")
            await page.unroute(RECEIPT, block_receipt)
            await page.evaluate("window.dispatchEvent(new Event('online'))")
            await expect(await composer(page)).to_have_value("", timeout=120000)
            await grid(page, await active(page))
            assert await history_count(page, saved) == 1
            passed("lost_success_response_receipt_recovery_without_repost")

            async def expire_session(route):
                async def expire(db):
                    await db.execute(text("UPDATE auth_sessions SET expires_at=now()-interval '1 second' WHERE user_id=:id"), {"id": state["user"]})
                await database(expire, write=True)
                await route.continue_()
            await page.route(POST, expire_session)
            field = await composer(page)
            await field.fill("Ручки Tippo от 8000 шт")
            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/requests"), timeout=120000) as pending:
                await field.press("Enter")
            assert (await pending.value).status == 401
            await page.unroute(POST, expire_session)
            await expect(page.get_by_role("heading", name="Вход")).to_be_visible(timeout=120000)
            await page.locator('input[name="name"]').fill(state["name"])
            await page.locator('input[name="password"]').fill(password)
            await page.get_by_role("button", name="Войти", exact=True).click()
            await expect(await composer(page)).to_have_value("Ручки Tippo от 8000 шт")
            await send(page, "Ручки Tippo от 8000 шт")
            assert (await read(page, f"/projects/{p['id']}/collection"))["selected_ids"] == [selected]
            passed("actual_expired_session_login_draft_collection")

            committed_event, release = asyncio.Event(), asyncio.Event()
            async def delayed(route):
                response = await route.fetch(timeout=120000)
                assert response.ok
                committed_event.set()
                await release.wait()
                await route.fulfill(response=response)
            await page.route(POST, delayed)
            field = await composer(page)
            await field.fill("Карандаши")
            await field.press("Enter")
            try:
                await asyncio.wait_for(committed_event.wait(), 120)
                second = await context.new_page()
                await second.goto(BASE, wait_until="domcontentloaded")
                newer, _, _ = await send(second, "Бутылки 750 мл")
                await page.bring_to_front()
                await page.evaluate("window.dispatchEvent(new Event('focus'))")
                await grid(page, newer)
                await page.evaluate("""() => {window.auditHeadings=[];new MutationObserver(()=>{const title=document.querySelector('.research-heading h1')?.textContent;if(title)window.auditHeadings.push(title)}).observe(document.querySelector('.research-content'),{subtree:true,childList:true,characterData:true});}""")
                release.set()
                await idle(page)
                await grid(page, newer)
                headings = await page.evaluate("window.auditHeadings")
                assert "Карандаши" not in headings
                await second.close()
                passed("late_response_after_newer_tab_keeps_active_grid")
            finally:
                release.set()
            await context.close()
            await browser.close()
    finally:
        evidence["cleanup"] = await cleanup(state)
        RESULT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    if sys.argv[1:] == ["cleanup"]:
        asyncio.run(cleanup(json.loads(LEDGER.read_text(encoding="utf8"))))
    else:
        asyncio.run(main())
