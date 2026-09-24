import asyncio
import json
from pathlib import Path
from app.ai.local import OllamaProvider
from app.application.research import ResearchService
from app.application.research_refinement import ResearchRefinementEngine
from app.config import Settings
from app.database.connection import create_database
from app.database.research import ResearchRepository


async def main():
    settings=Settings();engine,sessions=create_database(settings.database_url)
    report=json.loads(Path('.local/research/acceptance/bottles.json').read_text(encoding='utf-8'))
    repository=ResearchRepository(sessions)
    research=await repository.get(report['research_id'])
    model=OllamaProvider(settings.llm_base_url,settings.llm_model,settings.llm_timeout_seconds,settings.llm_structured_retries)
    service=ResearchService(repository,[],settings,model)
    message='Нужны бутылки, которые выглядят дороже своей цены'
    decision=await ResearchRefinementEngine(service).decide(message,research)
    output={'model':settings.llm_model,'message':message,'decision':decision.model_dump(mode='json')}
    Path('.local/research/acceptance/model.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False))
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
