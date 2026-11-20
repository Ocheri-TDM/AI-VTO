"""Opt-in PostgreSQL SQL semantics in a freshly owned isolated schema."""

import os
import re
from uuid import uuid4

import pytest
import test_contextual_facets as cases
from sqlalchemy import text
from test_product_grid import service

from app.config import Settings
from app.database.connection import create_database
from app.database.conversations import ConversationRepository
from app.database.models import Base
from app.database.repository import SearchRepository

pytestmark = pytest.mark.postgres


async def test_real_postgresql_contextual_facets():
    if os.getenv("CONTEXTUAL_FACETS_POSTGRES") != "1":
        pytest.skip("Explicit opt-in required; creates only its own temporary schema")
    settings = Settings(
        background_research_enabled=False, presentation_worker_enabled=False, auth_required=False
    )
    schema = "test_facets_" + uuid4().hex
    assert re.fullmatch(r"test_facets_[a-f0-9]{32}", schema)
    engine, _ = create_database(settings.database_url)
    created = False
    translated = engine.execution_options(schema_translate_map={None: schema})
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        async with translated.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        from sqlalchemy.ext.asyncio import async_sessionmaker

        sessions = async_sessionmaker(translated, expire_on_commit=False)
        grid = service(SearchRepository(sessions), settings)
        await grid.index.ingest(cases.labeled_products())
        for check in [
            cases.test_complete_pool_facets_and_last_page_value,
            cases.test_branch_or_self_exclusion_zero_and_visibility,
            cases.test_typed_characteristics_and_variant_precedence,
            cases.test_required_source_values_and_manual_common_choices,
            cases.test_or_within_attribute_and_and_between_attributes,
            cases.test_scope_validation_and_idempotent_projection,
            cases.test_false_catalog_category_and_multi_purpose_union,
        ]:
            chat = await ConversationRepository(sessions).create("PostgreSQL isolated facet fixture")
            await check((grid, chat))
    finally:
        if created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
