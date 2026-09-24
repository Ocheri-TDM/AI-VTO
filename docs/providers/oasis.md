# Oasis: исследование и контракт извлечения

## Research update, 2026-09-15/16

Oasis reactivated. Home and real bottle search opened in Chromium. Research uses
the observed form and `[data-catalog-product]`, follows `/page-N` links until no
next page, and queues every exact color variant. `variant_urls` entries are
objects with `url` and `color`, unlike string-only adapters. They are normalized
at the traversal boundary. A failed card retains a retry cursor and cannot make
the branch COMPLETE. Live bottle run encountered an HTTP error on one card.
Properties from `description.overview.main` now enrich research attributes.
Legacy page/product limits apply only to `search()`, not `research_listing()`.

Проверено 2026-09-08: обычный Chromium через Playwright, без авторизации,
регистрации, подмены browser identity и обхода защит. Воспроизведение:
`python scripts/research_oasis.py --search термокружка --label search`.
Локальные подробные снимки записываются в `.local/research/oasis/`.

## Поиск и страницы результатов

- [Главная](https://www.oasiscatalog.com/) содержит JSON-LD `WebSite` /
  `SearchAction.target = https://www.oasiscatalog.com/srch?q={query}`.
  Реальная форма: `form[action="/srch"] input[name="q"]`.
  После hydration placeholder меняется с `Поиск` на `поиск`, поэтому
  регистрозависимый поиск placeholder ненадёжен.
- В Chromium ввод `термокружка` и Enter открыли
  [категорию термокружек](https://www.oasiscatalog.com/categories/kuhnya-i-posuda/termokruzhki-i-termosi/termokruzhki?sort=popularity).
  Сервер может перенаправить поиск в подходящую категорию.
- На странице 48 групп товаров. `data-catalog-product` содержит JSON:
  `id`, `groupId`, `link`, `name`, `prices`, `stock`, `colorsProduct`.
  `colorsProduct[]` содержит `id`, `colorName`, `name`, `url` каждого цвета.
  Остаток основной карточки нельзя присваивать другим цветам.
- `a.pagination__btn` содержит реальные URL следующих страниц, например
  [страница 2](https://www.oasiscatalog.com/categories/kuhnya-i-posuda/termokruzhki-i-termosi/termokruzhki/page-2).
  Есть кнопка «Показать следующие 48». Адаптер использует опубликованные
  ссылки с сохранением query string, ограничивает страницы и число карточек.
  Прямое открытие page-2 с `sort=popularity` в одном из исследовательских
  контекстов вернуло HTTP 403: результат обхода нельзя считать полным.

## Карточка, цена, цвет и изображения

Исследованы [красный Sense](https://www.oasiscatalog.com/item/1-000091955)
и [тёмно-синий Sense](https://www.oasiscatalog.com/item/1-000091961).

- JSON-LD `Product` содержит `name`, `description`, `sku`, `offers.price`,
  `offers.priceCurrency = RUB`, `image[]`. JSON-LD `InStock` не содержит
  количества и сам по себе не подтверждает достаточный остаток.
- `data-product` содержит JSON полного активного варианта. `id` соответствует
  `/item/{id}`; `photosBlock.types` перечисляет варианты, их ссылки, названия
  цветов и цены. `description.overview.main` содержит явную характеристику
  `Цвет товара`; характеристику `Цвет гравировки` использовать нельзя.
- `photosBlock.productPhotos[].big` содержит исходные JPG URL на `s.a-5.ru`.
  Это более крупные исходники, чем превью JSON-LD. Файлы не скачиваются,
  автоматическое удаление фона не вызывается.
- Цена — публичная клиентская цена единицы в RUB, без самостоятельных
  предположений о дилерской скидке или доплатах нанесения. Конвертацию
  выполняет общий `CurrencyService`, а не адаптер.

## Остаток именно выбранного варианта

`data-product.settings.productWarehouses.main` обозначает склад «Москва».
`sizes[]` содержит строки с `id` варианта и числовыми `stock`, `reserve`.
Для обычного товара совпадают ID карточки и единственной строки sizes.
Адаптер берёт числовой `stock` только строки с точным ID; сумма размеров
и общий stock цветовой группы не подменяют этот остаток.

При проверке тёмно-синего `1-000091961`: `stock = 1891`, `reserve = 330`;
в браузере отображалось `1 891 + 330`, наведение на `+330` показало
«В резерве». `stock` уже представляет отдельно доступные единицы: резерв
не прибавляется и повторно не вычитается. `external` — часть того же
наличия на внешнем складе, также не прибавляется.

`transit` («В пути») и `fabric` («На фабрике») исключаются. В исследованной
карточке `transit.summary.stock = 0`, но `transit.sizes[].stock = 1000`,
а `fabric.summary.stock = 1000`: суммирование всех полей дало бы неверный
остаток и двойной учёт. На этапе 1 используется только подтверждённый
склад Москва; Европа/удалённые склады сохраняются как metadata, но не
расширяют принимаемый остаток без отдельной политики логистики.

## Network и ограничения

В обычной сессии наблюдались `/category/autocomplete?q=…`, `/user/info`,
`/cart/info`, `/site/options`, `/product-visualizer/{id}/data`. Дополнительный
API не требуется: приоритетные structured data уже содержат необходимые
поля. Аккаунтные ответы не используются адаптером и не сохраняются.

[robots.txt](https://www.oasiscatalog.com/robots.txt) запрещает индексацию
поисковых/query URL. Реализация предназначена для ограниченного поиска
по пользовательскому запросу, не для индексации/импорта каталога;
масштабирование требует согласования режима доступа с поставщиком.

Сайт может менять HTML/JSON, цены, остатки, блокировать запросы или требовать
CAPTCHA. Неизвестный stock остаётся `null`; schema drift возвращает
контролируемую ошибку. CAPTCHA передаётся как `SUPPLIER_CAPTCHA_REQUIRED`,
без обхода; HTTP 403 не выдаётся за отсутствие товаров. Ошибки отдельных
карточек и ограничение глубины отражаются в warnings/semantic traces.

Числа выше — доказательство формата на момент исследования, не runtime
fallback. Fixtures используются только в тестах.
# Final verification — 2026-09-18

Supplier-first navigation reads published `/categories/…` links. Initial ordinary Chromium home returned 403 without redirect; later roots/listing/exact product succeeded. Access denial is intermittent; its server-side cause is unknown. Coverage remains PARTIAL. Evidence: `../provider-status.md`, `.local/final/provider-live.json`, `.local/final/catalogs`.
