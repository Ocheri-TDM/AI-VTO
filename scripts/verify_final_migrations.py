"""Upgrade an empty isolated database to the current head and inspect required schema."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
from sqlalchemy.engine import make_url

from app.config import Settings


async def main():
    base=make_url(Settings().database_url)
    name='souvenir_migration_'+uuid4().hex[:10]
    connection=await asyncpg.connect(base.set(drivername='postgresql',database='postgres').render_as_string(hide_password=False))
    await connection.execute(f'CREATE DATABASE "{name}"')
    await connection.close()
    env={**os.environ,'DATABASE_URL':base.set(database=name).render_as_string(hide_password=False)}
    run=await asyncio.to_thread(subprocess.run,[sys.executable,'-m','alembic','-c','apps/api/alembic.ini','upgrade','head'],env=env,capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    connection=await asyncpg.connect(base.set(drivername='postgresql',database=name).render_as_string(hide_password=False))
    head=await connection.fetchval('SELECT version_num FROM alembic_version')
    tables=await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    names={row['tablename'] for row in tables}
    assert {'catalog_runs','catalog_seen','taxonomy_suggestions','research_sessions','supplier_offers'}<=names
    report={'fresh_database':name,'migration_head':head,'table_count':len(names),'upgrade_success':True}
    Path('.local/final/migrations.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report));await connection.close()


if __name__=='__main__':asyncio.run(main())
