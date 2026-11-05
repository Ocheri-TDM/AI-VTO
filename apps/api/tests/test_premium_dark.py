"""Template-version isolation and optical/typographic safety of Premium Dark 3."""

import copy
from contextlib import closing

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw
from test_presentation_design import content

from app.application.presentation_assets import store_upload
from app.application.presentation_layouts import PagePainter, illuminated_background
from app.application.presentation_render import luminance, photo, render_document, theme_roles
from app.domain.presentation_design import deterministic_plan, validate_plan


def assets_for(root, items):
    assets = {}
    for n, item in enumerate(items):
        image = Image.new('RGBA', (260, 380), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle((60, 30, 200, 350), 12,
                                                fill=['#3576B1', '#888888', '#161616'][n % 3])
        import io
        data = io.BytesIO()
        image.save(data, format='PNG')
        assets[item['offer_id']] = {'key': store_upload(root, 'control', data.getvalue(), prepared=True)}
    return assets


def test_optical_placement_uses_visible_alpha_and_preserves_white_and_faint_edges(tmp_path):
    class Canvas:
        def drawImage(self, image, x, y, width, height, **kwargs):
            self.image, self.rect = image, (x, y, width, height)

    for padding in (2, 80):
        image = Image.new('RGBA', (40 + padding * 2, 100 + padding * 2))
        ImageDraw.Draw(image).rectangle((padding, padding, padding + 39, padding + 99), fill='white')
        image.putpixel((padding, padding), (1, 2, 3, 1))
        path = tmp_path / f'{padding}.png'
        image.save(path)
        canvas = Canvas()
        photo(canvas, path, 0, 0, 200, 300, optical=True)
        assert canvas.rect == (40, 0, 120, 300)
        assert canvas.image.getSize() == (40, 100)
        assert canvas.image.getRGBData()[:3] == bytes([1, 2, 3])
        assert canvas.image.getRGBData()[3:6] == bytes([255, 255, 255])


def test_dark_object_has_no_standard_oval_in_v3_but_legacy_path_is_retained(tmp_path):
    value = content()
    assets = assets_for(tmp_path, value['items'])

    class Canvas:
        ovals = 0

        def setFillColor(self, color):
            pass

        def ellipse(self, *args, **kwargs):
            self.ovals += 1

        def drawImage(self, *args, **kwargs):
            pass

    for version, ovals in [(2, 1), (3, 0)]:
        canvas = Canvas()
        painter = PagePainter(canvas, tmp_path, value['settings'], theme_roles(value['settings']['palette']),
                              assets, 'premium-dark', version)
        painter.dark = True
        painter.object(value['items'][2], (50, 50, 200, 300))
        assert canvas.ovals == ovals


def test_illumination_is_deterministic_nonuniform_and_palette_based():
    image = illuminated_background('#163E52', '#C9AB78', True)
    assert image.tobytes() == illuminated_background('#163E52', '#C9AB78', True).tobytes()
    assert image.getpixel((345, 135)) != image.getpixel((0, 0))
    assert image.tobytes() != illuminated_background('#143F32', '#C9AB78', True).tobytes()


@pytest.mark.parametrize('base', ['#FFFFFF', '#808080', '#A9B3C9'])
def test_pale_client_palette_keeps_dark_background_and_readable_type(base):
    image = illuminated_background(base, '#FFFFFF', True)
    for y in range(0, image.height, 13):
        for x in range(0, image.width, 13):
            color = '#' + ''.join(f'{v:02x}' for v in image.getpixel((x, y)))
            assert (luminance('#F5F4EF') + .05) / (luminance(color) + .05) >= 4.5


@pytest.mark.parametrize('version', [2, 3])
def test_persisted_version_plan_replays_with_all_variant_facts(tmp_path, version):
    value = content()
    value['settings'].update(style='premium-dark', style_version=version)
    value['items'][0].update(brand=None, unit_price_kzt=None)
    value['design_plan'] = deterministic_plan(value['items'], value['settings'], value['category_order']).model_dump()
    original = copy.deepcopy(value)
    assets = assets_for(tmp_path, value['items'])
    result = render_document(tmp_path, f'v{version}', value, assets)
    replay = render_document(tmp_path, f'v{version}-replay', value, assets)
    assert result['sha256'] == replay['sha256']
    assert value == original
    assert result['design_plan']['style_version'] == version
    assert result['placements'] == ['vto:1', 'vto:2', 'vto:3'] and result['page_count'] == 2
    with pdfium.PdfDocument(str(tmp_path / result['pdf'])) as pdf:
        with closing(pdf[1]) as page, closing(page.get_textpage()) as tp:
            text = tp.get_text_range()
            assert 'Цена по запросу' in text and '1 002 ₸' in text and '1 003 ₸' in text
            assert 'синий' in text.casefold() and 'серый' in text.casefold() and 'чёрный' in text.casefold()
            if version == 3:
                assert text.count('Рюкзак-мешок Melango') == 1


def test_saved_plan_cannot_silently_switch_template_version():
    value = content()
    value['settings'].update(style='premium-dark', style_version=2)
    plan = deterministic_plan(value['items'], value['settings'], value['category_order']).model_dump()
    with pytest.raises(ValueError, match='STYLE_VERSION_MISMATCH'):
        validate_plan(plan, value['items'], value['settings'] | {'style_version': 3})
    with pytest.raises(ValueError, match='UNSUPPORTED_STYLE_VERSION'):
        deterministic_plan(value['items'], value['settings'] | {'style': 'editorial', 'style_version': 3}, ['backpack'])


@pytest.mark.parametrize('logo_on', [False, True])
def test_long_company_and_subtitle_do_not_collide_with_cover_label(tmp_path, logo_on):
    value = content()
    value['settings'].update(style='premium-dark', style_version=3,
                             client='Объединённая научно-производственная компания развития технологий',
                             subtitle='Подборка для команды: реальные варианты, единое оформление.', use_logo=logo_on)
    assets = assets_for(tmp_path, value['items'])
    if logo_on:
        value['settings']['logo'] = assets[value['items'][0]['offer_id']]['key']
    result = render_document(tmp_path, 'long-cover', value, assets)
    assert result['page_count'] == 2


async def test_new_generation_upgrades_only_new_revision_and_retry_keeps_legacy(repository, settings):
    from app.application.presentations import PresentationService
    from app.database.models import PresentationDocument, PresentationRevision, Project, User

    value = content()
    value['settings'].update(style='premium-dark', style_version=2)
    service = PresentationService(repository.sessions, settings, None)
    async with repository.sessions.begin() as db:
        db.add(User(id='owner', display_name='owner', normalized_name='owner', password_hash='unused'))
        await db.flush()
        db.add(Project(id='project', user_id='owner', name='control'))
        await db.flush()
        db.add(PresentationDocument(id='doc', project_id='project', user_id='owner', version=1, draft=value))
        await db.flush()
        db.add(PresentationRevision(id='legacy', document_id='doc', source_version=1,
                                    input=value, status='FAILED', attempts=1))
    retry = await service.enqueue('doc', 'owner', 1)
    assert retry['id'] == 'legacy'
    async with repository.sessions.begin() as db:
        legacy = await db.get(PresentationRevision, 'legacy')
        assert legacy.input['settings']['style_version'] == 2
        legacy.status = 'READY'
        doc = await db.get(PresentationDocument, 'doc')
        doc.version = 2
    latest = await service.enqueue('doc', 'owner', 2)
    assert latest['id'] != 'legacy'
    async with repository.sessions() as db:
        assert (await db.get(PresentationRevision, latest['id'])).input['settings']['style_version'] == 3
        assert (await db.get(PresentationRevision, 'legacy')).input == value
        assert (await db.get(PresentationDocument, 'doc')).draft == value


async def test_local_planner_uses_the_requested_template_without_extra_calls():
    from app.application.presentation_design import prepare_design

    value = content()
    value['settings'].update(style='premium-dark', style_version=3, auto_design=True)
    calls = []

    class Model:
        base_url = 'http://127.0.0.1:11434'

        async def complete(self, messages, schema):
            calls.append(messages)
            assert schema.model_json_schema()['properties']['style_version']['const'] == 3
            return deterministic_plan(value['items'], value['settings'], value['category_order'])

    result = await prepare_design(Model(), value, [], timeout=1)
    assert len(calls) == 1 and result['ai_used'] and result['design_plan']['style_version'] == 3
