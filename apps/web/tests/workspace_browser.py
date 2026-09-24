"""Chromium contract/UX tests. All mock products are scoped to Playwright routes."""

import asyncio
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import async_playwright, expect

OUTPUT = Path(".local/stage3")
BASE = os.getenv("WEB_TEST_URL", "http://127.0.0.1:3001")


class FixtureAPI:
    def __init__(self):
        self.projects = []
        self.chats = {}
        self.fail_selection = False
        self.fail_workspace = 0
        self.calls = []

    def result(self, chat):
        pool = chat["pool"]
        state = chat["state"]
        f = state["filters"]
        products = [
            p
            for p in pool
            if (f["categories"] is None or p["category"] in f["categories"])
            and p["category"] not in f["excluded_categories"]
            and (
                not f["budget"]
                or (
                    f["budget"].get("max") is None
                    or p["price_kzt"] <= f["budget"]["max"]
                )
                and (
                    f["budget"].get("min") is None
                    or p["price_kzt"] >= f["budget"]["min"]
                )
            )
            and (
                f["selection"] == "all"
                or (p["id"] in state["selected_ids"]) == (f["selection"] == "selected")
            )
        ]
        if state["sorting"].startswith("price_"):
            products.sort(
                key=lambda p: p["price_kzt"], reverse=state["sorting"] == "price_desc"
            )
        now = datetime.now(timezone.utc)
        session = {
            "id": "session-" + chat["id"],
            "status": "partial",
            "intent": {
                "raw_query": "Тёмно-синий мерч",
                "quantity": 300,
                "categories": ["backpack", "pen"],
                "colors": ["NAVY"],
                "budget": None,
            },
            "products": products,
            "suppliers": [
                {"supplier": "gifts", "status": "completed", "warnings": ["LIMIT"]},
                {"supplier": "ucontay", "status": "failed"},
            ],
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=50)).isoformat(),
            "cache_hit": False,
        }
        return {
            "assistant_message": "Подборка обновлена.",
            "action": "SHOW_RESULTS",
            "session": session if pool and not chat["expired"] else None,
            "search_session_id": session["id"]
            if pool and not chat["expired"]
            else None,
            "selected_product_ids": state["selected_ids"],
            "selected_products": [p for p in pool if p["id"] in state["selected_ids"]],
            "pool_count": len(pool),
            "state_version": state["version"],
            "active_state": copy.deepcopy(state),
            "can_undo": bool(chat["history"]),
            "facets": {
                "categories": list(dict.fromkeys(p["category"] for p in pool)),
                "colors": ["NAVY", "BLACK"],
            },
            "result_summary": {
                "visible_count": len(products),
                "selected_count": len(state["selected_ids"]),
            },
        }

    def create(self, name):
        key = str(len(self.chats) + 1)
        project = {
            "id": "p" + key,
            "name": name,
            "client_name": None,
            "chat_id": key,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary": "",
        }
        self.projects.append(project)
        self.chats[key] = {
            "id": key,
            "project": project,
            "expired": False,
            "last_intent": {},
            "messages": [],
            "pool": [],
            "history": [],
            "state": {
                "version": 0,
                "sorting": "relevance",
                "selected_ids": [],
                "filters": {
                    "categories": None,
                    "excluded_categories": [],
                    "colors": None,
                    "excluded_colors": [],
                    "budget": None,
                    "quantity": 300,
                    "hidden_ids": [],
                    "selection": "all",
                },
            },
        }
        return key

    @staticmethod
    def products():
        return [
            {
                "id": str(i),
                "name": (
                    "Рюкзак городской для ноутбука, тёмно-синий, с отделением для документов"
                    if i < 6
                    else "Ручка металлическая, тёмно-синяя"
                )
                + f" {i + 1}",
                "category": "backpack" if i < 6 else "pen",
                "description": "Лаконичный аксессуар для рабочего дня.",
                "price_kzt": 9999999 if i == 8 else 4500 + i * 1300,
                "colors": [{"original_color": "navy", "normalized_color": "NAVY"}],
                "primary_image": f"https://fixture.test/image-{i}.svg",
                "images": [],
                "duplicate_group_id": None,
            }
            for i in range(9)
        ]

    async def route(self, route):
        request = route.request
        path = request.url.split("/api", 1)[1]
        data = request.post_data_json if request.post_data else {}
        self.calls.append((request.method, path, data))

        async def reply(value, status=200):
            await route.fulfill(
                status=status,
                content_type="application/json",
                body=json.dumps(value, ensure_ascii=False),
            )

        if path == "/projects":
            return await reply(self.projects)
        if path.startswith("/projects/"):
            project = next(p for p in self.projects if p["id"] == path.split("/")[-1])
            if request.method == "DELETE":
                self.projects.remove(project)
                return await route.fulfill(status=204)
            project.update(data)
            return await reply(project)
        if path == "/chats":
            return await reply({"id": self.create(data["project_name"])}, 201)
        key = path.split("/")[2]
        if path.startswith("/searches/"):
            product = copy.deepcopy(self.products()[int(path.split("/")[-1])])
            product.update(
                supplier="gifts",
                stock_quantity=999,
                source_url="https://gifts.ru/id/1",
                original_price="100",
                original_currency="RUB",
                metadata={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            return await reply(product)
        chat = self.chats[key]
        if path.endswith("/workspace"):
            if self.fail_workspace:
                code = self.fail_workspace
                self.fail_workspace = 0
                if code == 410:
                    chat["expired"] = True
                return await reply({}, code)
            if data.get("restore") == "undo":
                chat["state"] = chat["history"].pop()
            else:
                chat["history"].append(copy.deepcopy(chat["state"]))
                if data.get("restore") == "reset":
                    chat["state"]["filters"].update(
                        categories=None, budget=None, colors=None, selection="all"
                    )
                if "filters" in data:
                    chat["state"]["filters"] = data["filters"]
                if "sorting" in data:
                    chat["state"]["sorting"] = data["sorting"]
            chat["state"]["version"] += 1
            return await reply(self.result(chat))
        if path.endswith("/selection"):
            await asyncio.sleep(0.35)
            if self.fail_selection:
                self.fail_selection = False
                return await reply({}, 503)
            chat["history"].append(copy.deepcopy(chat["state"]))
            selected = chat["state"]["selected_ids"]
            ids = data.get(
                "product_ids",
                [
                    p["id"]
                    for p in self.result(chat)["session"]["products"][
                        : data.get("count", 5)
                    ]
                ],
            )
            chat["state"]["selected_ids"] = (
                []
                if data["mode"] == "clear"
                else [x for x in selected if x not in ids]
                if data["mode"] == "remove"
                else list(dict.fromkeys(selected + ids))
                if data["mode"] == "add"
                else ids
            )
            chat["state"]["version"] += 1
            return await reply(self.result(chat))
        if path.endswith("/messages/stream"):
            message = data["message"]
            chat["messages"].append(
                {"id": str(len(chat["messages"])), "role": "user", "content": message}
            )
            await asyncio.sleep(0.6)
            chat["history"].append(copy.deepcopy(chat["state"]))
            if not chat["pool"]:
                chat["pool"] = self.products()
                chat["last_intent"] = {"raw_query": message}
            if "дороже" in message:
                chat["state"]["filters"]["budget"] = {"min": None, "max": 10000}
            if "только рюкзаки" in message.lower():
                chat["state"]["filters"]["categories"] = ["backpack"]
            if "бутылки" in message.lower():
                bottle = copy.deepcopy(chat["pool"][0])
                bottle.update(
                    id="bottle", name="Бутылка для воды, navy", category="bottle"
                )
                chat["pool"].append(bottle)
                chat["state"]["filters"]["categories"] = ["backpack", "bottle"]
            chat["state"]["version"] += 1
            result = self.result(chat)
            result["action"] = (
                "EXPAND_RESULTS" if "бутылки" in message.lower() else "SEARCH"
            )
            chat["messages"].append(
                {
                    "id": str(len(chat["messages"])),
                    "role": "assistant",
                    "content": result["assistant_message"],
                }
            )
            events = [
                ("agent.started", {}),
                (
                    "tool.started",
                    {
                        "tool": "incremental_search"
                        if "бутылки" in message.lower()
                        else "search_products"
                    },
                ),
                ("agent.completed", result),
            ]
            return await route.fulfill(
                content_type="text/event-stream",
                body="".join(
                    f"event: {name}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
                    for name, value in events
                ),
            )
        return await reply(
            {
                **{
                    k: v
                    for k, v in chat.items()
                    if k not in ("pool", "state", "history")
                },
                "current": self.result(chat),
            }
        )


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    passed = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        fixture = FixtureAPI()
        await context.route("**/api/**", fixture.route)

        async def image_route(route):
            if "image-8" in route.request.url:
                return await route.fulfill(status=404)
            await route.fulfill(
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600"><rect width="600" height="600" fill="#f0f0eb"/><rect x="175" y="150" width="250" height="310" rx="65" fill="#243b56"/><path d="M240 165V125Q300 70 360 125V165" stroke="#243b56" stroke-width="18" fill="none"/><rect x="207" y="315" width="186" height="108" rx="22" fill="#304e6f"/><path d="M215 330H385" stroke="#8193a4" stroke-width="4"/></svg>',
            )

        await context.route("https://fixture.test/**", image_route)
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(BASE)
        await expect(page.locator("#initial-query")).to_be_enabled()
        passed.append("initial project screen")
        await page.screenshot(path=str(OUTPUT / "initial.png"), full_page=True)
        await page.get_by_role("button", name="Новый проект", exact=True).click()
        await page.get_by_label("Название проекта").fill("Halyk Tech Gifts")
        await page.get_by_role("button", name="Создать проект", exact=True).click()
        await expect(page.locator(".workspace-heading h1")).to_have_text(
            "Halyk Tech Gifts"
        )
        await page.locator("#initial-query").fill(
            "Темно-синие рюкзаки и ручки, 300 шт."
        )
        await page.get_by_role("button", name="Начать подбор").click()
        await expect(page.locator(".skeleton-grid")).to_be_visible()
        passed.append("initial search loading / semantic activity")
        await expect(page.locator(".studio-main .product-card")).to_have_count(9)
        passed.append("search results / SSE completion")
        text = await page.locator(".studio-main .product-grid").inner_text()
        assert not any(
            x in text.lower()
            for x in ["gifts", "ucontay", "sku", "stock", "поставщик", "на складе"]
        )
        passed.append("card privacy")
        await page.locator('.studio-main .product-card').last.scroll_into_view_if_needed()
        await expect(page.locator('.studio-main .product-card').last.locator('.missing-image')).to_be_visible()
        passed.append('broken image fallback')
        await page.locator('.studio-main .image-details-button').first.click()
        await expect(page.locator('.product-dialog')).to_be_visible()
        assert not await page.locator('.product-dialog .details-section').evaluate('(node)=>node.open')
        await page.get_by_text('Техническая информация',exact=True).click()
        await expect(page.locator('.product-dialog').get_by_text('999 шт.',exact=True)).to_be_visible()
        await page.keyboard.press('Escape')
        passed.append('details privacy / modal keyboard dismissal')
        first = page.locator(".studio-main .product-checkbox input").first
        await first.check()
        await expect(first).to_be_disabled()
        await expect(first).to_be_enabled()
        await expect(first).to_be_checked()
        passed.append("manual server selection")
        await page.reload()
        await expect(
            page.locator(".studio-main .product-checkbox input").first
        ).to_be_checked()
        passed.append("reload project/chat/selection")
        fixture.fail_selection = True
        second = page.locator(".studio-main .product-checkbox input").nth(1)
        await second.check()
        await expect(second).to_be_checked()
        await expect(second).not_to_be_checked()
        passed.append("optimistic rollback")
        await page.get_by_role("button", name="Закрыть уведомление").click()
        await page.get_by_role('button',name='Выбрать лучших',exact=True).click()
        await expect(page.locator('.selection-trigger')).to_have_text('Выбрано 5')
        await page.get_by_role('button',name='Отменить',exact=True).click()
        await expect(page.locator('.selection-trigger')).to_have_text('Выбрано 1')
        passed.append('bulk top N selection / undo')
        await page.locator("#search-query").fill("Убери всё дороже 10000")
        await page.locator("#search-query").press("Enter")
        await expect(page.locator(".studio-main .product-card")).to_have_count(5)
        await expect(
            page.get_by_role("button", name="Убрать фильтр: До 10 000 ₸")
        ).to_be_visible()
        passed.append("chat filters / chips / grid sync")
        await page.get_by_role("button", name="Фильтры", exact=True).click()
        await expect(page.get_by_label("До", exact=True)).to_have_value("10000")
        await page.get_by_label("До", exact=True).fill("1")
        await page.get_by_role("button", name="Применить фильтры").click()
        await expect(
            page.get_by_role("heading", name="Фильтры скрыли все товары")
        ).to_be_visible()
        passed.append("manual filter / empty filtered state")
        await page.get_by_role("button", name="Выбрано 1", exact=True).first.click()
        await expect(page.locator(".selected-sheet .product-card")).to_have_count(1)
        passed.append("selected drawer retains hidden products")
        await page.keyboard.press("Escape")
        await page.get_by_role("button", name="Отменить", exact=True).click()
        await expect(page.locator(".studio-main .product-card")).to_have_count(5)
        passed.append("undo")
        await page.get_by_role("button", name="Убрать фильтр: До 10 000 ₸").click()
        await expect(page.locator(".studio-main .product-card")).to_have_count(9)
        await page.get_by_label("Сортировка товаров").select_option("price_desc")
        await expect(
            page.locator(".studio-main .product-meta strong").first
        ).to_have_text("9 999 999 ₸")
        passed.append("server sorting")
        await page.locator("#search-query").fill("Оставь только рюкзаки")
        await page.locator("#search-query").press("Enter")
        await expect(page.locator(".studio-main .product-card")).to_have_count(6)
        await page.locator("#search-query").fill("Добавь бутылки")
        await page.locator("#search-query").press("Enter")
        await expect(page.locator(".studio-main .product-card")).to_have_count(7)
        passed.append("incremental search / grid update")
        await expect(page.locator(".partial-warning")).to_be_visible()
        passed.append("partial source failure")
        for width, height in [
            (1920, 1080),
            (1440, 900),
            (1280, 800),
            (768, 1024),
            (430, 932),
            (390, 844),
        ]:
            await page.set_viewport_size({"width": width, "height": height})
            await page.screenshot(
                path=str(OUTPUT / f"workspace-{width}.png"), full_page=True
            )
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), width
            if width < 768:
                await page.get_by_role("button", name="AI", exact=True).click()
                await expect(page.locator(".chat-sheet")).to_be_visible()
                await page.screenshot(
                    path=str(OUTPUT / f"chat-{width}.png"), full_page=True
                )
                await page.keyboard.press("Tab")
                assert await page.evaluate('!!document.activeElement.closest("dialog")')
                await page.keyboard.press("Escape")
        passed.append("six viewports / mobile chat / focus trap / Escape / overflow")
        await page.set_viewport_size({"width": 1440, "height": 900})
        await page.get_by_role("button", name="Новый проект", exact=True).click()
        await page.get_by_label("Название проекта").fill("Second project")
        await page.get_by_role("button", name="Создать проект", exact=True).click()
        await expect(page.locator("#initial-query")).to_be_visible()
        await page.get_by_role(
            "button", name="Halyk Tech Gifts", exact=False
        ).first.click()
        await expect(page.locator(".studio-main .product-card")).to_have_count(7)
        passed.append("project switch")
        for status in [409, 429]:
            fixture.fail_workspace = status
            await page.get_by_label("Сортировка товаров").select_option("price_asc")
            await expect(page.locator(".feedback")).to_be_visible()
            await page.get_by_role("button", name="Закрыть уведомление").click()
        passed.append("409 / 429 feedback")
        fixture.fail_workspace = 410
        await page.get_by_role("button", name="Отменить", exact=True).click()
        await expect(
            page.get_by_role("heading", name="Результаты поиска устарели")
        ).to_be_visible()
        passed.append("410 expired state preserves chat")
        assert not errors, errors
        (OUTPUT / "frontend-tests.json").write_text(
            json.dumps(
                {"passed": passed, "page_errors": errors}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(
            f"{len(passed)} grouped frontend scenarios passed; six viewports checked."
        )
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
