"""User regressions: empty-category semantics, role ownership and punctuation."""

import asyncio

import pytest
from quality.regression_acceptance import CONTROLS

from app.database.models import Base
from app.domain.catalog_identity import BrandRegistry, resolve_identity
from app.main import create_app


async def test_hydration_does_not_consume_write_slots_and_cancel_releases(settings):
    from app.api.request_safety import Admission

    admission = Admission(settings)
    async with (
        admission.enter("owner", per_user_limit=4, operation="read"),
        admission.enter("owner", per_user_limit=4, operation="read"),
    ):
        async with admission.enter("owner", per_user_limit=2, operation="write"):
            assert admission.active == 3
    assert admission.active == 0 and admission.queued == 0
    entered = asyncio.Event()

    async def wait_read():
        async with admission.enter("cancel-owner", per_user_limit=1, operation="read"):
            entered.set()

    async with admission.enter("cancel-owner", per_user_limit=1, operation="read"):
        task = asyncio.create_task(wait_read())
        await asyncio.sleep(0.01)
        assert not entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert admission.active == 0 and admission.queued == 0


def test_normalization_preserves_identifiers_decimals_and_original_spans():
    from app.domain.query_constraints import parse_constraints
    from app.domain.query_normalization import normalize_query

    raw = "темно. синий. цвет. ручки. тираж. 200. GF-12449.40 USB-C 0,5 л 35 224,51"
    value = normalize_query(raw)
    assert "темно-синий" in value.text
    for literal in ["GF-12449.40", "USB-C", "0,5 л", "35 224,51"]:
        assert literal in value.text
    token = value.trace()["tokens"][0]
    assert raw[token["original_start"] : token["original_end"]] == "темно. синий"
    constraints, _ = parse_constraints("флешки USB-C на 64 ГБ, 200 штук")
    assert (
        constraints.storage_gb == 64
        and constraints.connectors == ["USB-C"]
        and constraints.quantity_min == 200
    )


@pytest.mark.parametrize("name,query", CONTROLS.items())
async def test_four_product_branches_without_catalog_population(settings, name, query):
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    try:
        intent = await app.state.research.grid_plan(query)
        assert set(intent.categories) == {
            "pen",
            "notebook",
            "thermomug",
            "cap" if name in ("A", "C") else "baseballcap",
        }
        assert len(intent.branches) == 4
        assert intent.models == [] and intent.brands == []
        assert intent.colors == ["RED"] and intent.quantity == 200
        assert all(b.colors == ["RED"] and b.quantity == 200 and not b.models for b in intent.branches)
    finally:
        await app.state.engine.dispose()


def test_unknown_list_fragment_is_not_a_model_and_explicit_model_is_retained():
    registry = BrandRegistry([])
    unknown = resolve_identity("ручки и квантосборники", registry)
    assert not unknown.models and unknown.unresolved_products == ["квантосборники"]
    explicit = resolve_identity("ручка модели Xylophone", registry)
    assert explicit.models == ["Xylophone"]
    known = resolve_identity("ручка Tippo", registry, [("Ручка Tippo", "Ручка Tippo", "Brand")])
    assert known.models == ["Tippo"]


async def test_local_colors_do_not_become_global(settings):
    app = create_app(settings, [])
    async with app.state.engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    try:
        intent = await app.state.research.grid_plan("красные ручки, синие блокноты и чёрные кепки")
        assert {b.category: b.colors for b in intent.branches} == {
            "pen": ["RED"],
            "notebook": ["BLUE"],
            "cap": ["BLACK"],
        }
    finally:
        await app.state.engine.dispose()
