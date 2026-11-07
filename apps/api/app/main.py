import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
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
    if providers is None:
        from app.providers.vto import VTOProvider

        providers = [VTOProvider(settings)]
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
            settings.llm_concurrency,
            settings.llm_queue_limit,
            settings.llm_queue_timeout_seconds,
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
    from app.domain.query_understanding import QueryUnderstandingService

    index = IndexRepository(sessions, settings)
    query_understanding = QueryUnderstandingService(sessions)
    from app.domain.conversation_router import ConversationQueryRouter

    conversation_router = ConversationQueryRouter(query_understanding)
    research = IndexedResearchService(
        ResearchRepository(sessions), providers, settings, model, index, query_understanding
    )
    background = BackgroundIndex(index, providers, settings)
    orchestrator.research = research
    from app.application.project_queries import ProjectQueryService

    project_queries = ProjectQueryService(sessions, research, conversation_router)
    from app.application.collections import CollectionService

    collections = CollectionService(sessions, index)
    research.collections = collections
    from app.application.presentations import PresentationService

    presentations = PresentationService(sessions, settings, research.repository, collections, model)
    from app.application.commercial_offers import CommercialOfferService

    offers = CommercialOfferService(presentations)

    async def publish_index_update(job):
        await research.on_index_update(job)

    background.on_update = publish_index_update

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
        await project_queries.recover()
        cleanup = asyncio.create_task(clean_cache(), name="cache-cleanup")

        async def loop_probe():
            while True:
                before = time.perf_counter()
                await asyncio.sleep(0.1)
                app.state.loop_lag.append(max(0, time.perf_counter() - before - 0.1))

        probe = asyncio.create_task(loop_probe(), name="event-loop-probe")
        if settings.background_research_enabled:
            background.start()
        if settings.presentation_worker_enabled:
            presentations.start()
        try:
            yield
        finally:
            cleanup.cancel()
            probe.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            with suppress(asyncio.CancelledError):
                await probe
            await search.close()
            await presentations.close()
            await background.close()
            await research.close()
            for provider in providers:
                if hasattr(provider, "close"):
                    await provider.close()
            await engine.dispose()

    app = FastAPI(
        title="Souvenir Search API",
        version="0.3.0",
        lifespan=lifespan,
        description="VTO HTTP catalog, durable index and local AI refinement. Last-known stock remains available with its observation date.",
    )
    app.state.repository, app.state.search = repository, search
    app.state.engine = engine
    app.state.loop_lag = deque(maxlen=2048)
    app.state.sessions, app.state.settings = sessions, settings
    app.state.conversations, app.state.orchestrator = conversations, orchestrator
    app.state.research = research
    app.state.query_understanding = query_understanding
    app.state.project_queries = project_queries
    app.state.presentations = presentations
    app.state.offers = offers
    app.state.collections = collections
    app.state.conversation_router = conversation_router
    app.state.index, app.state.background_index = index, background
    from app.api.request_safety import Admission, RequestBoundary, RequestProblem, problem_response

    admission = Admission(settings)
    app.state.offer_public_admission = Admission(
        settings.model_copy(
            update={"search_concurrency": 16, "search_queue_limit": 8, "search_requests_per_minute": 180}
        )
    )
    app.state.admission = admission
    app.state.stream_admission = Admission(
        settings.model_copy(update={"search_concurrency": 16, "search_queue_limit": 0})
    )
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    from app.api.projects import router as projects_router

    app.include_router(projects_router)
    from app.api.research import router as research_router

    app.include_router(research_router)
    from app.api.diagnostics import router as diagnostics_router

    app.include_router(diagnostics_router)
    from app.api.project_queries import router as project_queries_router

    app.include_router(project_queries_router)
    from app.api.presentations import router as presentations_router

    app.include_router(presentations_router)
    from app.api.collections import router as collections_router

    app.include_router(collections_router)
    from app.api.commercial_offers import public_router
    from app.api.commercial_offers import router as offers_router

    app.include_router(offers_router)
    app.include_router(public_router)

    @app.middleware("http")
    async def authenticated_user(request: Request, call_next):
        protected = request.url.path.startswith(
            (
                "/api/chats",
                "/api/projects",
                "/api/researches",
                "/api/searches",
                "/api/hard-requests",
                "/api/presentations",
                "/api/offers",
            )
        )
        if protected and settings.auth_required:
            user = await current_user(request)
            if user is None:
                return JSONResponse(status_code=401, content={"detail": "Authentication required"})
            request.state.user = user
        search_operation = (
            request.method in ("GET", "POST", "PATCH", "PUT", "DELETE")
            and request.url.path.startswith(
                ("/api/projects", "/api/researches", "/api/chats", "/api/searches")
            )
            and not request.url.path.endswith("/events")
        )
        if search_operation:
            try:
                # Workspace hydration reads history, collection and research in
                # parallel. Keep writes at two; allow four bounded read calls.
                async with admission.enter(
                    getattr(getattr(request.state, "user", None), "id", None),
                    per_user_limit=4 if request.method == "GET" else 2,
                    operation="read" if request.method == "GET" else "write",
                ):
                    response = await call_next(request)
                    if request.url.path.endswith("/offers"):
                        from app.api.commercial_offers import PUBLIC_HEADERS

                        response.headers.update(PUBLIC_HEADERS)
                    return response
            except RequestProblem as exc:
                return problem_response(exc, getattr(request.state, "trace_id", ""))
        response = await call_next(request)
        if request.url.path.startswith(("/api/public/offers", "/api/offers")):
            from app.api.commercial_offers import PUBLIC_HEADERS

            response.headers.update(PUBLIC_HEADERS)
        return response

    app.add_middleware(RequestBoundary, settings=settings)

    @app.exception_handler(ConnectionError)
    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: Exception):
        log_event(
            "database_error", error_type=type(exc).__name__, trace_id=getattr(_request.state, "trace_id", "")
        )
        return problem_response(
            RequestProblem("DATABASE_UNAVAILABLE", "База данных временно недоступна.", 503, "database"),
            getattr(_request.state, "trace_id", ""),
        )

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def intent_error(_request: Request, _exc: ValidationError):
        return problem_response(
            RequestProblem("INVALID_REQUEST", "Не удалось разобрать запрос. Проверьте тираж и фильтры.", 422),
            getattr(_request.state, "trace_id", ""),
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
