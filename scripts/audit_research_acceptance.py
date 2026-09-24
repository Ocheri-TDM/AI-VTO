"""Revalidate recorded live observations after taxonomy fixes; no fabricated observations."""
import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.database.connection import create_database
from app.database.research import ResearchRepository
from app.domain.intent import CategoryResolver


async def main():
    engine, sessions = create_database(Settings().database_url)
    repository = ResearchRepository(sessions)
    try:
        for label in ('bottles', 'conference'):
            path = Path(f'.local/research/acceptance/{label}.json')
            report = json.loads(path.read_text(encoding='utf-8'))
            research = await repository.get(report['research_id'])
            rejected = []
            for product in research.products:
                if product.category == 'bottle' and 'bottle' not in CategoryResolver().classify(product.name):
                    product.metadata['research_invalid'] = True
                    rejected.append(product.id)
            await repository.save(research)
            report['validation_audit_excluded_ids'] = rejected
            report['pool_count'] = sum(not p.metadata.get('research_invalid') for p in research.products)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(label, 'valid pool', report['pool_count'], 'audit exclusions', len(rejected))
            for supplier in dict.fromkeys(b.supplier for b in research.coverage):
                branches = [b for b in research.coverage if b.supplier == supplier]
                print(supplier, 'pages', sum(b.pages_scanned for b in branches), 'seen', sum(b.products_seen for b in branches),
                      'accepted observations', sum(b.products_validated for b in branches),
                      'rejected', sum(b.products_rejected for b in branches),
                      'valid pool', sum(p.supplier == supplier and not p.metadata.get('research_invalid') for p in research.products),
                      'statuses', sorted({b.status.value for b in branches}),
                      'errors', sorted({b.error_code for b in branches if b.error_code}))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
