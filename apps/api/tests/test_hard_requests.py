import asyncio

import pytest
from sqlalchemy import select

from app.application.hard_requests import HardRequestService
from app.database.models import Chat, HardRequestRecord, Project, ProjectQuery, User
from app.domain.hard_request import HardRequestIntent, deterministic_hard_request
from app.domain.models import AvailabilityRecord, ColorGroup, Product, ProductColor, utcnow
from app.domain.taxonomy import UniversalCategoryResolver


def test_hard_request_parser_preserves_explicit_categories_and_quantity():
    intent = deterministic_hard_request(
        "Найди-собери 50 наборов состоящих из 5 категорий: "
        "ручки, карандаши, панамы, рюкзаки, бутылки. От 250 шт."
    )
    assert intent.target_sets == 50
    assert intent.categories == ["ручки", "карандаши", "панамы", "рюкзаки", "бутылки"]
    assert intent.parsed_categories_count == intent.declared_categories_count == 5
    assert intent.quantity.minimum == 250
    assert intent.candidates_per_position == 3
    assert intent.color.mode == "BASE_COLORS"


def test_exact_composite_without_quantity_keeps_target_separate_from_procurement():
    intent = deterministic_hard_request(
        "собери 50 наборов из футболок, ручек, бутылок и кружек"
    )
    assert intent.target_sets == 50
    assert intent.categories == ["футболок", "ручек", "бутылок", "кружек"]
    assert intent.required_quantity is None
    assert intent.quantity.required is False
    assert all(intent.effective(category).required_quantity is None for category in intent.categories)


async def test_initial_assembly_failure_is_terminal_and_preserves_active_workspace(repository):
    user = User(id="failure-user", display_name="Failure", normalized_name="failure", password_hash="x")
    project = Project(id="failure-project", user_id=user.id, name="Failure", active_query_id="good-query")
    previous = ProjectQuery(
        id="good-query",
        project_id=project.id,
        type="STANDARD",
        query_text="карандаш",
        status="ACTIVE",
    )
    chat = Chat(id="failure-chat", project_id=project.id, user_id=user.id)
    async with repository.sessions.begin() as db:
        db.add(user)
    async with repository.sessions.begin() as db:
        db.add(project)
    async with repository.sessions.begin() as db:
        db.add_all([previous, chat])

    service = HardRequestService(repository.sessions, None, None)

    async def fail_after_record(_request_id, initial=5):
        raise RuntimeError("injected after record creation")

    service._prepare_and_build = fail_after_record
    plan = deterministic_hard_request("собери 10 наборов из ручек и кружек")
    with pytest.raises(RuntimeError, match="injected"):
        await service.create_hard(project.id, plan.original_query or "собери 10 наборов из ручек и кружек", user.id, plan)

    async with repository.sessions() as db:
        failed = await db.scalar(select(HardRequestRecord).where(HardRequestRecord.project_id == project.id))
        restored = await db.get(Project, project.id)
        failed_query = await db.get(ProjectQuery, failed.query_id)
    assert failed.status == "FAILED"
    assert failed_query.status == "FAILED"
    assert failed.metrics["failure_code"] == "RuntimeError"
    assert failed.metrics["failure_stage"] == "INITIAL_ASSEMBLY"
    assert failed.metrics["failed_at"]
    assert restored.active_query_id == previous.id


async def test_composite_undo_walks_committed_semantic_history(repository):
    user = User(id="undo-user", display_name="Undo", normalized_name="undo", password_hash="x")
    project = Project(id="undo-project", user_id=user.id, name="Undo")
    chat = Chat(id="undo-chat", project_id=project.id, user_id=user.id)
    async with repository.sessions.begin() as db:
        db.add(user)
    async with repository.sessions.begin() as db:
        db.add(project)
    async with repository.sessions.begin() as db:
        db.add(chat)
    service = HardRequestService(repository.sessions, None, None)

    async def finish(request_id, initial=5):
        async with repository.sessions.begin() as db:
            row = await db.get(HardRequestRecord, request_id)
            row.status = "COMPLETED"

    service._prepare_and_build = finish
    initial = deterministic_hard_request("собери 5 наборов из ручек, футболок")
    first = await service.create_hard(project.id, "initial", user.id, initial)
    red = service.intent_mutations.apply(
        initial,
        service.intent_mutations.plan(initial, "футболки красные", "футболки красные"),
    )
    second = await service.create_hard(
        project.id, "red", user.id, red, first["id"], {"type": "INTENT_PATCH"}
    )
    blue = service.intent_mutations.apply(
        red,
        service.intent_mutations.plan(red, "ручки синие", "ручки синие"),
    )
    await service.create_hard(
        project.id, "blue", user.id, blue, first["id"], {"type": "INTENT_PATCH"}
    )

    def position_colors(value, concept):
        parsed = HardRequestIntent.model_validate(value["plan"])
        position = next(
            item for item in parsed.positions
            if concept in service.intent_mutations._concepts(item.category_text)
        )
        return (position.constraints.color or parsed.color).values

    undo_one = await service.patch_active(project.id, "отмени", user.id)
    assert position_colors(undo_one, "pen") == []
    assert position_colors(undo_one, "tshirt") == ["RED"]
    undo_two = await service.patch_active(project.id, "отмени", user.id)
    assert position_colors(undo_two, "pen") == []
    assert position_colors(undo_two, "tshirt") == []
    assert second["revision_number"] == 2


async def test_delayed_recovery_cannot_reactivate_newer_workspace(repository):
    user = User(id="race-user", display_name="Race", normalized_name="race", password_hash="x")
    project = Project(id="race-project", user_id=user.id, name="Race", active_query_id="old-query")
    old_query = ProjectQuery(id="old-query", project_id=project.id, type="HARD", query_text="old")
    new_query = ProjectQuery(id="new-query", project_id=project.id, type="STANDARD", query_text="карандаш")
    plan = deterministic_hard_request("собери 5 наборов из зонтов, ручек")
    record = HardRequestRecord(
        id="race-request", project_id=project.id, query_id=old_query.id,
        original_text=plan.original_query or "old", parsed_plan=plan.model_dump(mode="json"),
        status="COMPLETED", target_cases=5,
    )
    async with repository.sessions.begin() as db:
        db.add(user)
    async with repository.sessions.begin() as db:
        db.add(project)
    async with repository.sessions.begin() as db:
        db.add_all([old_query, new_query])
    async with repository.sessions.begin() as db:
        db.add(record)

    service = HardRequestService(repository.sessions, None, None)
    published = []

    async def capture(*args, **kwargs):
        published.append((args, kwargs))

    service.create_hard = capture
    await service.mutation_locks[project.id].acquire()
    delayed = asyncio.create_task(service.on_index_update({
        "checkpoint": {"composite_request_ids": [record.id]}
    }))
    await asyncio.sleep(0)
    async with repository.sessions.begin() as db:
        current = await db.get(Project, project.id)
        current.active_query_id = new_query.id
    service.mutation_locks[project.id].release()
    await delayed

    async with repository.sessions() as db:
        current = await db.get(Project, project.id)
    assert current.active_query_id == new_query.id
    assert published == []


async def test_failed_mutation_releases_project_lock_and_preserves_active_revision(repository):
    user = User(id="mutation-failure-user", display_name="Failure", normalized_name="mutation-failure", password_hash="x")
    project = Project(id="mutation-failure-project", user_id=user.id, name="Failure")
    chat = Chat(id="mutation-failure-chat", project_id=project.id, user_id=user.id)
    async with repository.sessions.begin() as db:
        db.add(user)
    async with repository.sessions.begin() as db:
        db.add(project)
    async with repository.sessions.begin() as db:
        db.add(chat)
    service = HardRequestService(repository.sessions, None, None)

    async def finish(request_id, initial=5):
        async with repository.sessions.begin() as db:
            row = await db.get(HardRequestRecord, request_id)
            row.status = "COMPLETED"

    service._prepare_and_build = finish
    plan = deterministic_hard_request("собери 5 наборов из ручек, футболок")
    initial = await service.create_hard(project.id, "initial", user.id, plan)

    async def fail(_request_id, initial=5):
        raise RuntimeError("injected mutation failure after lock")

    service._prepare_and_build = fail
    with pytest.raises(RuntimeError, match="injected mutation failure"):
        await service.patch_active(project.id, "футболки красные", user.id)
    async with repository.sessions() as db:
        current = await db.get(Project, project.id)
        failed = await db.scalar(
            select(HardRequestRecord)
            .where(HardRequestRecord.project_id == project.id, HardRequestRecord.status == "FAILED")
        )
    assert current.active_query_id == initial["query_id"]
    assert failed is not None

    service._prepare_and_build = finish
    recovered = await asyncio.wait_for(
        service.patch_active(project.id, "ручки синие", user.id), timeout=5
    )
    assert recovered["status"] == "COMPLETED"
    assert recovered["query_id"] != initial["query_id"]


def test_hard_request_parser_reports_category_count_mismatch():
    intent = deterministic_hard_request("Собери 10 наборов из 5 категорий: ручки, панамы, бутылки, рюкзаки. От 100 шт.")
    assert intent.parsed_categories_count == 4
    assert intent.category_count_mismatch is True


def product(records, stale=False):
    return Product(id="offer", supplier="supplier", source_url="https://example.test/p", name="name",
        availability_records=records, metadata={"availability_stale": stale})


def colored(identifier, color, records):
    return Product(id=identifier, supplier="supplier", source_url=f"https://example.test/{identifier}",
        name=identifier, colors=[ProductColor(original_color=color, normalized_color=ColorGroup(color))],
        availability_records=records)


def record(state, free=None):
    return AvailabilityRecord(state=state, free_quantity=free, observed_at=utcnow(),
        parser_version="test", source_label="fixture")


def test_hard_request_availability_tiers_do_not_use_total_or_reserve():
    service = HardRequestService(None, None, None)
    assert service.tier(product([record("ON_HAND", 250)]), 250) == "A"
    assert service.tier(product([record("ON_HAND", 100), record("INCOMING", 150)]), 250) == "B"
    assert service.tier(product([record("PREORDER", 250)]), 250) == "C"
    assert service.tier(product([record("PREORDER")]), 250) == "D"
    assert service.tier(product([record("ON_HAND", 250)], stale=True), 250) == "D"


def test_no_quantity_keeps_catalog_eligible_without_inventing_current_stock():
    service = HardRequestService(None, None, None)
    plan = deterministic_hard_request("Собери 10 наборов из ручек и кружек")
    unknown = product([])
    current = product([record("ON_HAND", 3)])
    incoming = product([record("INCOMING", 5)])
    assert service.quantity_eligible(unknown, plan)
    assert service.tier(unknown, plan.required_quantity) == "D"
    assert service.tier(current, plan.required_quantity) == "A"
    assert service.tier(incoming, plan.required_quantity) == "B"


def test_explicit_blue_and_quantity_are_filtered_before_assembly():
    service = HardRequestService(None, None, None)
    plan = deterministic_hard_request(
        "Собери 10 наборов из зонтов, дождевиков, носков, футболок от 250 шт синего цвета"
    )
    blue = colored("blue", "BLUE", [record("ON_HAND", 500)])
    navy = colored("navy", "NAVY", [record("ON_HAND", 300)])
    black = colored("black", "BLACK", [record("ON_HAND", 1000)])
    unknown = colored("unknown", "UNKNOWN", [record("ON_HAND", 1000)])
    no_stock = colored("no-stock", "BLUE", [record("ON_HAND")])
    zero = colored("zero", "BLUE", [AvailabilityRecord(state="ON_HAND", free_quantity=0,
        total_quantity=1000, reserved_quantity=500, observed_at=utcnow(), parser_version="test",
        source_label="fixture")])
    assert service.color_eligible(blue, plan) and service.quantity_eligible(blue, plan)
    assert service.color_eligible(navy, plan) and service.quantity_eligible(navy, plan)
    assert not service.color_eligible(black, plan)
    assert not service.color_eligible(unknown, plan)
    assert not service.quantity_eligible(no_stock, plan)
    assert not service.quantity_eligible(zero, plan)


def test_case_plan_never_reuses_products_or_invents_requested_cases():
    service = HardRequestService(None, None, None)
    plan = deterministic_hard_request("Собери 10 наборов из A, B, C от 250 шт")
    pools = {
        category: [colored(f"{category}-{number}", "BLUE", [record("ON_HAND", 500)]) for number in range(7)]
        for category in plan.categories
    }
    identities = {product.id: product.id for values in pools.values() for product in values}
    result = service.assembly_plan(plan, pools, identities)
    assert len(result) == 7
    selected = [identities[positions[0].id] for case in result for _, positions in case]
    assert len(selected) == len(set(selected))
    fingerprints = [tuple(positions[0].id for _, positions in case) for case in result]
    assert len(fingerprints) == len(set(fingerprints))


def test_position_colors_override_global_constraints():
    intent = deterministic_hard_request(
        "Собери 50 наборов из зонтов синего цвета, дождевиков черного, "
        "носков белого, футболок красного от 250 шт"
    )
    expected = {
        "зонтов": {"BLUE", "NAVY", "DARK_BLUE", "LIGHT_BLUE", "ROYAL_BLUE", "BLUE_GREY"},
        "дождевиков": {"BLACK"}, "носков": {"WHITE"}, "футболок": {"RED"},
    }
    assert intent.quantity.minimum == 250
    assert intent.color.mode == "BASE_COLORS"
    for position in intent.positions:
        effective = intent.effective(position.category_text)
        assert set(effective.color.values) == expected[position.category_text]


def test_global_color_applies_except_position_override():
    intent = deterministic_hard_request(
        "Собери 20 красных наборов: зонты синие, рюкзаки, футболки, бутылки. От 100 шт"
    )
    assert intent.target_sets == 20
    assert intent.effective("зонты").color.values != ["RED"]
    assert all(intent.effective(category).color.values == ["RED"]
               for category in ("рюкзаки", "футболки", "бутылки"))


def test_genitive_mug_form_uses_shared_taxonomy():
    assert "mug" in UniversalCategoryResolver().resolve("кружек")


def test_hard_color_and_stock_use_the_same_offer_variant():
    service = HardRequestService(None, None, None)
    plan = deterministic_hard_request("Собери 2 набора из зонтов, ручек от 20 шт синего цвета")
    blue = colored("family-blue", "BLUE", [record("ON_HAND", 50)])
    black = colored("family-black", "BLACK", [record("ON_HAND", 500)])
    assert service.color_eligible(blue, plan) and service.tier(blue, 20) == "A"
    assert not service.color_eligible(black, plan)


async def test_identical_composite_query_reuses_bounded_validated_intent():
    class Model:
        calls = 0

        async def complete(self, _messages, response_model):
            self.calls += 1
            return response_model(
                categories=["зонты", "ручки"],
                quantity={"minimum": 20},
            )

    model = Model()
    service = HardRequestService(None, None, None, model)
    query = "Собери наборы из зонтов, ручек от 20 шт"
    first = await service.parse(query)
    second = await service.parse("  СОБЕРИ   НАБОРЫ ИЗ ЗОНТОВ, РУЧЕК ОТ 20 ШТ  ")
    assert first == second
    assert model.calls == 0
    assert first.query_understanding["llm_used"] is False
    assert first.query_understanding["llm_reason"] is None


async def test_complete_typo_composite_uses_deterministic_fast_path(repository):
    from app.domain.query_understanding import QueryUnderstandingService

    class Model:
        calls = 0

        async def complete(self, _messages, _response_model):
            self.calls += 1
            raise AssertionError("a structurally complete request must not call Qwen")

    model = Model()
    service = HardRequestService(
        repository.sessions,
        None,
        None,
        model,
        QueryUnderstandingService(repository.sessions),
    )
    result = await service.parse(
        "Сабери 50 набороф из синих зонтоф, чорных дождевеков, "
        "белых наскофф, красных футболак от 250 штук"
    )

    assert result.request_mode == "COMPOSITE"
    assert result.target_sets == 50
    assert result.quantity.minimum == 250
    assert len(result.positions) == 4
    assert [position.constraints.color.values[-1] for position in result.positions] == [
        "ROYAL_BLUE",
        "BLACK",
        "WHITE",
        "RED",
    ]
    assert model.calls == 0
    assert result.query_understanding["llm_used"] is False
    assert result.query_understanding["llm_reason"] is None
    assert result.query_understanding["intent_completeness"]["requires_llm"] is False


async def test_canonical_and_typo_composite_have_identical_structured_intent(repository):
    from app.domain.query_understanding import QueryUnderstandingService

    service = HardRequestService(
        repository.sessions,
        None,
        None,
        None,
        QueryUnderstandingService(repository.sessions),
    )
    canonical = await service.parse(
        "Собери 50 наборов из синих зонтов, черных дождевиков, "
        "белых носков, красных футболок от 250 штук"
    )
    typo = await service.parse(
        "Сабери 50 набороф из синих зонтоф, чорных дождевеков, "
        "белых наскофф, красных футболак от 250 штук"
    )

    def structure(plan):
        return {
            "mode": plan.request_mode,
            "target_sets": plan.target_sets,
            "minimum_quantity": plan.quantity.minimum,
            "positions": [
                {
                    "concepts": UniversalCategoryResolver().resolve(position.category_text),
                    "color": position.constraints.color.values if position.constraints.color else [],
                }
                for position in plan.positions
            ],
        }

    assert structure(typo) == structure(canonical)
    assert typo.query_understanding["llm_used"] is False


async def test_semantic_composite_escalates_with_machine_readable_reason():
    class Model:
        calls = 0

        async def complete(self, _messages, response_model):
            self.calls += 1
            return response_model(categories=["ручки", "блокноты"])

    model = Model()
    service = HardRequestService(None, None, None, model)
    result = await service.parse(
        "Собери более статусный набор из ручек, блокнотов для руководителей"
    )

    assert model.calls == 1
    assert result.query_understanding["llm_used"] is True
    assert result.query_understanding["llm_reason"] == "SEMANTIC_PREFERENCE"


async def test_quantity_phrase_ne_menee_does_not_force_llm():
    class Model:
        calls = 0

        async def complete(self, _messages, _response_model):
            self.calls += 1
            raise AssertionError("deterministic quantity must not escalate")

    model = Model()
    service = HardRequestService(None, None, None, model)
    result = await service.parse(
        "Собери наборы из зонтов, ручек и кружек не менее 250 штук"
    )
    assert result.quantity.minimum == 250
    assert result.query_understanding["llm_used"] is False
    assert model.calls == 0
