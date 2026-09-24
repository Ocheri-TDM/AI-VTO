import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.ai.basic import BasicIntentParser
from app.ai.local import OllamaProvider, OpenAICompatibleProvider
from app.api.auth import current_user
from app.api.auth import router as auth_router
from app.api.chats import router as chat_router
from app.api.routes import router
from app.application.orchestrator import Orchestrator
from app.application.search import SearchService
from app.browser import BrowserEngine
from app.config import Settings
from app.database.connection import create_database
from app.database.conversations import ConversationRepository
from app.database.repository import SearchRepository
from app.observability import configure_logging, log_event
from app.providers.base import SupplierProvider


def create_app(settings: Settings | None = None, providers: list[SupplierProvider] | None = None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = create_database(settings.database_url)
    repository = SearchRepository(sessions)
    browser = BrowserEngine(settings)
    if providers is None:
        from app.providers.artegifts import ArteGiftsProvider
        from app.providers.gifts import GiftsProvider
        from app.providers.happygifts import HappyGiftsProvider
        from app.providers.oasis import OasisProvider
        from app.providers.portobello import PortobelloProvider
        from app.providers.ucontay import UcontayProvider

        providers = [OasisProvider(browser, settings), GiftsProvider(browser, settings),
                     UcontayProvider(browser, settings), PortobelloProvider(browser, settings),
                     HappyGiftsProvider(browser, settings), ArteGiftsProvider(browser, settings)]
    search = SearchService(repository, providers, BasicIntentParser(), settings)
    model_class = {
        "basic": None,
        "ollama": OllamaProvider,
        "openai_compatible": OpenAICompatibleProvider,
    }.get(settings.llm_backend)
    if settings.llm_backend not in ("basic", "ollama", "openai_compatible"):
        raise ValueError("Unsupported LLM_BACKEND")
    model = (
        model_class(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
            settings.llm_structured_retries,
        )
        if model_class
        else None
    )
    conversations = ConversationRepository(sessions)
    orchestrator = Orchestrator(search, conversations, settings, model)
    from app.application.background_index import BackgroundIndex
    from app.application.index_research import IndexedResearchService
    from app.database.index import IndexRepository
    from app.database.research import ResearchRepository
    index = IndexRepository(sessions, settings)
    research = IndexedResearchService(ResearchRepository(sessions), providers, settings, model, index)
    background = BackgroundIndex(index,providers,settings)
    background.on_update = research.on_index_update
    orchestrator.research = research

    async def clean_cache() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                removed = await repository.purge_expired()
                if removed:
                    log_event("cache_expired_removed", sessions=removed)
            except (SQLAlchemyError, ConnectionError) as exc:
                log_event("cache_cleanup_error", error_type=type(exc).__name__)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        configure_logging()
        await repository.purge_expired()
        await repository.recover_interrupted()
        await conversations.recover_interrupted()
        await research.repository.recover()
        cleanup = asyncio.create_task(clean_cache(), name="cache-cleanup")
        if settings.background_research_enabled:
            background.start()
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            await search.close()
            await background.close()
            await research.close()
            await browser.close()
            await engine.dispose()

    app = FastAPI(
        title="Souvenir Search API",
        version="0.3.0",
        lifespan=lifespan,
        description="Durable supplier research and local AI refinement. Observations become stale after 60 minutes; research history remains available.",
    )
    app.state.repository, app.state.search, app.state.browser = repository, search, browser
    app.state.engine = engine
    app.state.sessions, app.state.settings = sessions, settings
    app.state.conversations, app.state.orchestrator = conversations, orchestrator
    app.state.research = research
    app.state.index, app.state.background_index = index, background
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    from app.api.projects import router as projects_router

    app.include_router(projects_router)
    from app.api.research import router as research_router
    app.include_router(research_router)
    from app.api.diagnostics import router as diagnostics_router
    app.include_router(diagnostics_router)

    @app.middleware("http")
    async def authenticated_user(request: Request, call_next):
        protected = request.url.path.startswith((
            "/api/chats", "/api/projects", "/api/researches", "/api/searches",
        ))
        if protected and settings.auth_required:
            user = await current_user(request)
            if user is None:
                return JSONResponse(status_code=401, content={"detail": "Authentication required"})
            request.state.user = user
        return await call_next(request)

    @app.exception_handler(ConnectionError)
    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: Exception):
        log_event("database_error", error_type=type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "База данных временно недоступна."})

    @app.exception_handler(ValidationError)
    async def intent_error(_request: Request, _exc: ValidationError):
        return JSONResponse(
            status_code=422, content={"detail": "Не удалось разобрать запрос. Проверьте тираж и фильтры."}
        )

    @app.get("/api/health", tags=["health"])
    async def health():
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "database": "ok",
            "llm_backend": settings.llm_backend,
            "rub_kzt_rate_configured": settings.rub_kzt_rate is not None,
            "suppliers": [p.supplier for p in providers],
        }

    return app
