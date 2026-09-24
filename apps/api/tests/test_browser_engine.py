import pytest

from app.browser import BrowserEngine
from app.domain.errors import SupplierError
from app.domain.models import ActionType

pytestmark = pytest.mark.browser


async def test_chromium_detects_challenge_and_closes_context(settings):
    engine = BrowserEngine(settings)
    try:
        async with engine.session("oasis", {"www.oasiscatalog.com"}) as session:
            await session.page.set_content(
                "<html><title>Verification</title><body>Подтвердите, что вы не робот</body></html>"
            )
            with pytest.raises(SupplierError) as caught:
                await session.check_challenge()
            assert caught.value.code == "SUPPLIER_CAPTCHA_REQUIRED"
            assert session.traces[-1].details["code"] == "SUPPLIER_CAPTCHA_REQUIRED"
        assert not engine._contexts
    finally:
        await engine.close()


async def test_navigation_allowlist_and_redacted_trace(settings):
    engine = BrowserEngine(settings)
    try:
        async with engine.session("oasis", {"www.oasiscatalog.com"}) as session:
            for url in (
                "http://127.0.0.1/",
                "https://evil.example/",
                "https://www.oasiscatalog.com.evil.example/",
                "file:///secret",
                "https://www.oasiscatalog.com:8080/",
            ):
                with pytest.raises(SupplierError) as caught:
                    await session.goto(url)
                assert caught.value.code == "SUPPLIER_INVALID_URL"
            session.record(
                ActionType.SEARCH,
                "https://www.oasiscatalog.com/search?query=private",
                query="secret",
                cookie="secret",
                count=1,
            )
            trace = session.traces[-1].model_dump_json()
            assert "private" not in trace and "secret" not in trace
            assert session.traces[-1].details == {"count": 1}
    finally:
        await engine.close()
