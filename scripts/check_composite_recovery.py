import asyncio
import json
import os
from pathlib import Path

import asyncpg

SOURCE = json.loads(
    Path(
        os.environ.get("COMPOSITE_SOURCE", ".local/composite-blue20-live-v2.json")
    ).read_text(encoding="utf-8-sig")
)


async def main():
    source = SOURCE
    project_id = source["project"]["id"]
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(url)
    request = await connection.fetchrow(
        """
      SELECT hr.id,hr.parent_request_id,hr.revision_number,hr.completed_cases,hr.target_cases,
             hr.status,hr.metrics,hr.updated_at
      FROM projects p JOIN project_queries pq ON pq.id=p.active_query_id
      JOIN hard_requests hr ON hr.query_id=pq.id WHERE p.id=$1
    """,
        project_id,
    )
    source_metrics = (
        source.get("initial", {}).get("metrics", {})
        or source.get("results", [{}])[0].get("request", {}).get("metrics", {})
    )
    jobs = await connection.fetch(
        """
      SELECT j.id,j.supplier,j.status,j.priority,j.error,j.checkpoint,j.metrics,j.updated_at,
             s.retry_after,s.lease_until,s.lease_owner,s.error supplier_error
      FROM catalog_sync_jobs j LEFT JOIN supplier_sync_states s ON s.supplier=j.supplier
      WHERE j.id=ANY($1::varchar[])
      ORDER BY updated_at
    """,
        source_metrics.get("availability_recovery_jobs", []),
    )
    blockers = await connection.fetch("""
      SELECT j.supplier,j.kind,j.status,j.priority,j.updated_at,s.lease_owner,s.lease_until,s.retry_after,s.error
      FROM catalog_sync_jobs j LEFT JOIN supplier_sync_states s ON s.supplier=j.supplier
      WHERE j.status='RUNNING' ORDER BY j.priority DESC
    """)
    print(
        json.dumps(
            {
                "active": dict(request) if request else None,
                "jobs": [dict(row) for row in jobs],
                "running_jobs": [dict(row) for row in blockers],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
