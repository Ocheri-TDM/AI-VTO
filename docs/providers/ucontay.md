# Ucontay — исследование и контракт адаптера

## Research update, 2026-09-15/16

Home and live bottle/powerbank search verified again. Research follows the observed
`a.pagination-next[href]` and reads all variants per product from the browser's
document response. It does not inherit the old stock-characteristic URL filters:
quantity is validated per exact variant after extraction. Original variant colors
must pass ColorNormalizer before matching the broad blue family. Raw attributes
remain evidence for material/brand/facets. Time budget yields LIMITED and preserves
the current page plus pending product URLs; no product-count limit is applied.

Проверено 8 сентября 2026 через обычный Playwright Chromium без авторизации.
Скрипты воспроизведения: `scripts/research_ucontay.py`,
`scripts/research_ucontay_search.py`; временные HTML/network snapshots лежат
в исключённой из Git `.research/ucontay/`.

## Реально наблюдаемый поиск

На [главной странице](https://ucontay.kz/) форма `form.header__search-form`
имеет action `/collection/all`, поле `input[name="q"]` и выбор складов.
Обработчик формы формирует GET с `q` и повторяемыми `characteristics[]`:

- `113475444`: В Алма-Ате;
- `108951931`: В Европе;
- `273463770`: Скрыть в поиске = Нет, добавляется самим сайтом.

При выборе обоих складов браузер перешёл на
[/collection/all?q=термокружка&characteristics[]=113475444&characteristics[]=108951931&characteristics[]=273463770](https://ucontay.kz/collection/all?q=%D1%82%D0%B5%D1%80%D0%BC%D0%BE%D0%BA%D1%80%D1%83%D0%B6%D0%BA%D0%B0&characteristics%5B%5D=113475444&characteristics%5B%5D=108951931&characteristics%5B%5D=273463770).
Первая страница содержит 36 карточек, следующая — ещё 21 на момент проверки.
Реальная ссылка `a.pagination-next` сохраняет query/filters и добавляет `page=2`.
Адаптер следует опубликованным ссылкам, ограничивая страницы и карточки настройками.

## Данные товара и варианты

Исследована [карточка RADMIR](https://ucontay.kz/product/termokruzhka-radmir-soft-touch).
HTML ответа содержит `data-product-json` — JSON InSales с `id`, `title`,
`url`, `images`, `option_names`, `variants`, `properties`, `characteristics`.
Вариант содержит отдельные `id`, `sku`, `price`, `quantity`, `image_id`,
`image_ids`, `option_values`, `variant_field_values`.

Сайт удаляет `data-product-json` из DOM после инициализации. Поэтому адаптер
читает **ответ документа, полученный именно навигацией Chromium**, затем
декодирует HTML-атрибут через HTMLParser и JSON. Это structured data сайта,
не отдельный guessed API и не HTTP-импорт каталога. В JSON-LD также есть
Product и BreadcrumbList, но variant JSON содержит более точные остатки.
Обычные browser network-запросы включают storefront cart и аналитику;
внешние запросы для нанесения/профиля не нужны и адаптер их не вызывает.

Ссылка с `?variant_id=710296740` проверена браузером: выбирается именно
тёмно-синий вариант. Один универсальный Product соответствует одному варианту.
Цвет получается через `option_names.title = Цвет` и соответствующий
`option_values.option_name_id`. Размеры не смешиваются с цветами.

## Остатки: доступно, резерв и ожидание

JS сайта выводит `variant.quantity` в `.js-product-card-quantity` как
«Доступно: N». Это подтверждено исходным обработчиком
`change_variant:insales:product` и Chromium. Числовое `quantity` — основной
источник доступного количества **конкретного варианта**, без суммирования цветов.

В таблице `.var-status` обнаружены такие поля `variant_field_values`:

| Склад | Доступно | Ожидание | Остаток | Резерв |
|---|---|---|---|---|
| Алматы | 13315 | 13314 | 13312 | 13313 |
| Москва | 13386 | 13385 | 13383 | 13384 |

Например, у розового RADMIR `quantity=1713`, остаток Алматы=1738,
резерв=25, доступно=1713. У тёмно-синего `quantity=0`: другие цвета в наличии
не делают тёмно-синий доступным. Значения ожидания и резерва никогда не
добавляются к доступному количеству. Если нет валидного числового `quantity`,
остаток остаётся неизвестным и domain AvailabilityService исключает вариант.

## Цена, изображения, ограничения

- `meta[name="shop-config"]` объявляет `currency_code=KZT`; цены берутся из
  `variant.price`, не из общей минимальной цены семьи. Конвертации в адаптере нет.
- Оригиналы: `images[].original_url`; основное изображение сопоставляется с
  `variant.image_id`, дополнительные — с `image_ids`. Все изображения семьи
  остаются доступными в metadata, чтобы не подменять цвет основной фотографии.
- Цены и остатки являются наблюдениями на fetched_at, без гарантии будущего резерва.
- Авторизованные скидки/конструктор нанесения находятся за отдельным доступом;
  используются публичные цены обычного посетителя, регистрация не выполняется.
- CAPTCHA для форм присутствует в shop-config даже при нормальной странице.
  Это не означает, что каталог заблокирован. Контролируемая ошибка
  `SUPPLIER_CAPTCHA_REQUIRED` возвращается только при активной проверке браузера.
- При изменении структуры адаптер сообщает `SUPPLIER_PARSING_ERROR` вместо
  создания фиктивных товаров. Выборка ограничена настройками, не полная выгрузка.
# Final verification — 2026-09-18

Supplier-first navigation reads published `/collection/…` links. InSales family JSON supplies variants; refresh groups family URLs. Live navigation/listing/product observed; 152 observations updated in a time-budgeted refresh. Checkpoints survive slices. Coverage remains PARTIAL; see `../provider-status.md` and `.local/final/quality.json`.
