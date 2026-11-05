"""Title provenance, composition novelty and HTTP request/retry isolation."""

import asyncio
import copy
from uuid import uuid4

import pytest
from test_premium_dark import assets_for
from test_presentation_design import content
from test_presentation_materials import materials_app  # noqa: F401

from app.application.presentation_assets import store_upload
from app.application.presentation_logo import DEFAULT_SHA256, freeze_logo, frozen_logo_path
from app.application.presentation_render import render_document
from app.database.models import PresentationRevision
from app.domain.presentation_composition import novelty, signature
from app.domain.presentation_design import deterministic_plan, validate_plan
from app.domain.presentation_palette import manual_plan


def title_value(root):
    value = content()
    settings = value['settings']
    settings.update(style='premium-dark', style_version=3, cover_policy=1, renderer_version=1,
                    palette_plan=manual_plan(settings['palette']), resolved_logo=freeze_logo(root, settings))
    return value


def test_real_default_custom_disabled_and_broken_active_logo_are_distinct(tmp_path):
    settings = {'use_logo': False, 'logo': None}
    default = freeze_logo(tmp_path, settings)
    assert default['hash'] == DEFAULT_SHA256 and default['source'] == 'default'
    from test_presentations import image_bytes
    key = store_upload(tmp_path, 'doc', image_bytes('red'), prepared=False)
    custom = freeze_logo(tmp_path, {'use_logo': True, 'logo': key})
    assert custom['source'] == 'custom' and custom['hash'] != default['hash']
    assert freeze_logo(tmp_path, {'use_logo': False, 'logo': key}) == default
    (tmp_path/key).write_bytes(b'broken')
    with pytest.raises(ValueError, match='клиентский'):
        freeze_logo(tmp_path, {'use_logo': True, 'logo': key})
    assert frozen_logo_path(tmp_path, custom).is_file()  # frozen previous bytes survive


def test_server_rejects_products_missing_title_or_wrong_logo_before_render(tmp_path):
    value = title_value(tmp_path)
    value['settings']['cover'] = False  # old checkbox cannot disable the new title policy
    plan = deterministic_plan(value['items'], value['settings'], value['category_order']).model_dump()
    assert plan['pages'][0]['layout'] == 'title' and plan['pages'][0]['product_refs'] == []
    for mutate in (lambda p: p['pages'][0].update(product_refs=['vto:1']),
                   lambda p: p['pages'][0].update(logo_ref='foreign'),
                   lambda p: p['pages'].pop(0),
                   lambda p: p['pages'][0].update(layout='cover')):
        bad = copy.deepcopy(plan)
        mutate(bad)
        with pytest.raises(ValueError):
            validate_plan(bad, value['items'], value['settings'])


@pytest.mark.parametrize('style', ['premium-dark', 'editorial', 'modern-graphic'])
def test_title_renderer_never_calls_product_components_and_replay_is_identical(tmp_path, style):
    value = title_value(tmp_path)
    value['settings'].update(style=style, style_version=3 if style == 'premium-dark' else 2)
    assets = assets_for(tmp_path, value['items'])
    first = render_document(tmp_path, 'first', value, assets)
    trace = first['component_trace']
    assert [c['component'] for c in trace if c['page'] == 1] == ['title_logo']
    assert [c['ref'] for c in trace if c['component'] == 'product_photo'] == [i['offer_id'] for i in value['items']]
    value['design_plan'] = first['design_plan']
    replay = render_document(tmp_path, 'replay', value, assets)
    assert replay['sha256'] == first['sha256']


def test_five_families_differ_by_real_geometry_without_ids_seed_or_time(tmp_path):
    value = title_value(tmp_path)
    recent = []
    for seed in range(5):
        value['settings']['design_seed'] = seed
        plan = deterministic_plan(value['items'], value['settings'], value['category_order'])
        result = novelty(plan, recent)
        assert result['status'] == 'DISTINCT'
        assert result['minimum_feature_changes'] is None or result['minimum_feature_changes'] >= 2
        recent.append(result)
        changed = plan.model_copy(deep=True)
        changed.design_seed += 100
        assert signature(changed) == signature(plan)
    assert len({p['hash'] for p in recent}) == 5
    assert novelty(plan, recent)['status'] == 'CONSTRAINED'


def test_unused_variant_parameter_does_not_count_as_structural_novelty(tmp_path):
    value = title_value(tmp_path)
    plan = deterministic_plan(value['items'], value['settings'], value['category_order'])
    other = plan.model_copy(deep=True)
    assert other.pages[1].layout == 'variants'
    other.pages[1].variant = 1  # same actual triptych geometry as variant 0
    assert signature(other) == signature(plan)


async def test_duplicate_key_new_generation_and_failed_retry_reuse_frozen_input(materials_app):  # noqa: F811
    app, client, identifier = materials_app
    base = '/api/presentations/' + identifier
    doc = (await client.get(base)).json()
    body = {'version': doc['version'], 'request_id': str(uuid4())}
    responses = await asyncio.gather(*(client.post(base+'/generate', json=body) for _ in range(2)))
    assert all(r.status_code == 202 for r in responses), [(r.status_code, r.text) for r in responses]
    assert responses[0].json()['id'] == responses[1].json()['id']
    first_id = responses[0].json()['id']
    async with app.state.sessions.begin() as db:
        row = await db.get(PresentationRevision, first_id)
        first_input = copy.deepcopy(row.input)
        row.status = 'FAILED'
        row.attempts = 1
    again = await client.post(base+'/generate', json=body)
    assert again.json()['id'] == first_id
    async with app.state.sessions.begin() as db:
        row = await db.get(PresentationRevision, first_id)
        assert row.input == first_input
        row.status = 'READY'
    fresh = await client.post(base+'/generate', json={'version': doc['version'], 'request_id': str(uuid4())})
    assert fresh.status_code == 202 and fresh.json()['id'] != first_id
    assert (await client.post(base+'/generate', json=body)).json()['id'] == first_id
    async with app.state.sessions() as db:
        row = await db.get(PresentationRevision, fresh.json()['id'])
        assert row.input['settings']['design_seed'] != first_input['settings']['design_seed']
        assert row.input['items'] == first_input['items']
        assert row.input['settings']['resolved_logo'] == first_input['settings']['resolved_logo']
