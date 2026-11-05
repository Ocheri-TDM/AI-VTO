from fastapi import APIRouter, HTTPException, Request

from app.application.index_quality import IndexQualityService

router = APIRouter(prefix="/api/dev", tags=["diagnostics"])


@router.get("/runtime")
async def runtime(request: Request):
    from app.api.auth import current_user

    if not request.app.state.settings.debug_agent or not await current_user(request):
        raise HTTPException(404)
    engine = request.app.state.engine
    pool = engine.pool

    def distribution(values):
        samples = sorted(values)
        return {
            "n": len(samples),
            "max": max(samples, default=0),
            "p50": samples[len(samples) // 2] if samples else 0,
            "p95": samples[min(len(samples) - 1, int(len(samples) * 0.95))] if samples else 0,
        }

    return {
        "pool_checkout_seconds": distribution(getattr(pool, "wait_seconds", [])),
        "pool_checked_out": pool.checkedout(),
        "pool_checkouts": getattr(pool, "checkout_count", 0),
        "sql_seconds": distribution(engine.sync_engine.sql_seconds),
        "event_loop_lag_seconds": distribution(request.app.state.loop_lag),
        "admission_active": request.app.state.admission.active,
        "admission_queued": request.app.state.admission.queued,
        "admission_rejected": request.app.state.admission.rejected,
    }


@router.get("/index")
async def index_quality(request: Request):
    if not request.app.state.research.settings.debug_agent:
        raise HTTPException(404)
    return await IndexQualityService(request.app.state.index, request.app.state.research.providers).report()
