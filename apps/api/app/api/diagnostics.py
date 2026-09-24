from fastapi import APIRouter, HTTPException, Request

from app.application.index_quality import IndexQualityService

router = APIRouter(prefix='/api/dev', tags=['diagnostics'])


@router.get('/index')
async def index_quality(request: Request):
    if not request.app.state.research.settings.debug_agent:
        raise HTTPException(404)
    return await IndexQualityService(request.app.state.index, request.app.state.research.providers).report()
