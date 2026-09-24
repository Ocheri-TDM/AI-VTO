import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, Route, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.config import Settings
from app.domain.errors import SupplierError
from app.domain.models import ActionType, BrowserAction
from app.observability import log_event, safe_url


class BrowserSession:
    def __init__(self, page: Page, supplier: str, allowed_hosts: set[str]):
        self.page = page
        self.supplier = supplier
        self.allowed_hosts = allowed_hosts
        self.traces: list[BrowserAction] = []
        self.pages_scanned = 0
        self.blocked = False

    def validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.allowed_hosts
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            raise SupplierError("SUPPLIER_INVALID_URL", "Адрес вне разрешённого сайта поставщика.")

    def record(self, action: ActionType, url: str | None = None, **details: object) -> None:
        # Semantic allowlist keeps queries, cookies, headers and page bodies out of logs/traces.
        allowed = {
            "count", "quantity", "currency", "field", "code", "page", "method",
            "variant_id", "status", "redirected",
        }
        clean = {key: value for key, value in details.items() if key in allowed}
        trace = BrowserAction(action=action, supplier=self.supplier, url=safe_url(url), details=clean)
        if len(self.traces) < 2000:
            self.traces.append(trace)
        log_event("browser_action", supplier=self.supplier, action=action.value, url=safe_url(url), **clean)

    async def check_challenge(self) -> None:
        title = (await self.page.title()).casefold()
        text = (await self.page.locator("body").inner_text(timeout=5000)).casefold()
        challenge_text = (
            "verify you are human",
            "подтвердите, что вы не робот",
            "подтвердите, что вы человек",
            "проверка, что вы не робот",
            "checking your browser",
            "проверка браузера",
        )
        challenge = any(marker in text for marker in challenge_text) or "just a moment" in title
        for frame in self.page.frames:
            if any(
                marker in frame.url
                for marker in ("/recaptcha/api2/bframe", "hcaptcha.com/captcha", "challenges.cloudflare.com")
            ):
                element = await frame.frame_element() if frame.parent_frame else None
                if element is not None and await element.is_visible():
                    challenge = True
        if challenge:
            self.blocked = True
            self.record(ActionType.ERROR, self.page.url, code="SUPPLIER_CAPTCHA_REQUIRED")
            raise SupplierError(
                "SUPPLIER_CAPTCHA_REQUIRED", "Поставщик требует проверку CAPTCHA. Нужна помощь пользователя."
            )

    async def goto(self, url: str, action: ActionType = ActionType.OPEN_PAGE) -> None:
        self.validate_url(url)
        self.record(action, url)
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded")
            self.pages_scanned += 1
            self.validate_url(self.page.url)
            await self.check_challenge()
            if response and response.status >= 400:
                self.record(
                    ActionType.ERROR, self.page.url, code="SUPPLIER_HTTP_ERROR",
                    status=response.status, redirected=self.page.url != url,
                )
                raise SupplierError(
                    "SUPPLIER_HTTP_ERROR",
                    f"Сайт поставщика вернул HTTP {response.status}.",
                    retryable=response.status in (429, 502, 503, 504),
                )
        except PlaywrightTimeoutError as exc:
            raise SupplierError(
                "SUPPLIER_TIMEOUT", "Превышено время ожидания страницы.", retryable=True
            ) from exc
        except PlaywrightError as exc:
            raise SupplierError(
                "SUPPLIER_BROWSER_ERROR", "Не удалось открыть страницу поставщика.", retryable=True
            ) from exc


class BrowserEngine:
    """One Chromium process with bounded isolated contexts; no persisted credentials."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(min(settings.browser_concurrency, settings.browser_page_limit))
        self._supplier_slots = {}
        self._idle = {}
        self.metrics = {}
        self._contexts: set[BrowserContext] = set()

    async def start(self) -> None:
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.settings.browser_headless)

    @asynccontextmanager
    async def session(self, supplier: str, allowed_hosts: set[str]) -> AsyncIterator[BrowserSession]:
        gate = self._supplier_slots.setdefault(supplier, asyncio.Semaphore(self.settings.provider_concurrency))
        async with gate, self._slots:
            await self.start()
            assert self._browser is not None
            key = (supplier, tuple(sorted(allowed_hosts)))
            idle = self._idle.setdefault(key, [])
            reused = bool(idle)
            if reused:
                context, page = idle.pop()
            else:
                if len(self._contexts) >= self.settings.browser_page_limit:
                    for pool in self._idle.values():
                        if pool:
                            old_context, _ = pool.pop()
                            await old_context.close()
                            self._contexts.discard(old_context)
                            break
                context = await self._browser.new_context(locale="ru-RU", accept_downloads=False)
                page = await context.new_page()
                self._contexts.add(context)
                counters = self.metrics.setdefault(supplier, {'network_requests':0, 'contexts_created':0, 'contexts_reused':0})
                counters['contexts_created'] += 1
                def count_request(_request):
                    counters['network_requests'] += 1
                page.on('request', count_request)
            if reused:
                self.metrics[supplier]['contexts_reused'] += 1
            context.set_default_timeout(self.settings.browser_timeout_ms)
            context.set_default_navigation_timeout(self.settings.browser_timeout_ms)
            session = BrowserSession(page, supplier, allowed_hosts)

            async def guard_navigation(route: Route) -> None:
                request = route.request
                if request.is_navigation_request() and request.frame == page.main_frame:
                    try:
                        session.validate_url(request.url)
                    except SupplierError:
                        await route.abort("blockedbyclient")
                        return
                await route.continue_()

            if not reused:
                await context.route("**/*", guard_navigation)
            try:
                yield session
            finally:
                if not page.is_closed() and not session.blocked:
                    idle.append((context,page))
                else:
                    await context.close()
                    self._contexts.discard(context)

    async def close(self) -> None:
        for context in list(self._contexts):
            await context.close()
        self._contexts.clear()
        self._idle.clear()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
