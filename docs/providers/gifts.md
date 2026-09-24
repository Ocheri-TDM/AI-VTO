# Gifts.ru — наблюдения Chromium, 9 сентября 2026

Активный поставщик. В Research composition root Oasis снова включён.

## Research update, 2026-09-15/16

Live search and exact Aquarius blue card `/id/129740` verified in Chromium.
Research walks every next-page link and every returned exact offer, preserving
pending URLs when the time budget expires. Product facts now also come from
`#j_product [itemprop=description]` and `.itm-opts-label` label/value rows:
Материал, Размеры, Объем в мл. Stock is still the exact variant's free quantity.
Search results include packaging **under a bottle**; category validation excludes
пакет/мешочек/коробка/чехол mentioning bottles rather than treating them as bottles.
The browser-visible ArticlesData literal remains parsed as JSON, never evaluated.

Поиск проверен отправкой `#j_search_input` из `#j_header_search_form`:
POST `/search/results`, поле `hash`, приводит к `/search/text/<query>`.
Переход на следующую страницу: `a.ctlg-pages-arrow.next[href]` (`/page2`).
Есть также «Показать ещё». Используем обычную навигацию, ограниченную настройками.

В результатах `window.Logrus.ArticlesData`: словарь семейств, массив блоков,
`items` содержит отдельные варианты: id, name, url, prices, stock, gallery.
Поиск может возвращать нерелевантные категории. Проверка категории остаётся в domain.
Строка stock содержит «На складе», «Свободно», «В пути»; последние два нельзя суммировать.

Точная карточка `https://gifts.ru/id/206709` проверена отдельно:
`#j_product h1[itemprop=name]`, `meta[itemprop=price]`,
`meta[itemprop=priceCurrency]` = RUB, `meta[itemprop=sku]`.
`tr.j_salearticle .itm-ord-qty input.j_qty` содержит placeholder свободного остатка.
В наблюдении: склад 1160, свободно 840, резерв 320, в пути 7000.
Берём 840. При нескольких строках не суммируем неизвестные размерные варианты:
остаток считаем неизвестным. Неизвестный остаток исключается SearchService.

Цвет — текст точного названия. Соседние цветовые варианты имеют отдельные /id/ URL.
Первая `g-gallery` содержит img с `data-hd` (2000 px) и `data-download`.
После гидрации изображения перемещены в открытый shadow DOM; Playwright его читает.
Нельзя брать фото соседних вариантов или галереи примеров нанесения.

В network наблюдалось обновление шапки, данных карточки через отдельный API не
потребовалось: хватает structured data и DOM. Регистрация не выполнялась.
CAPTCHA/403 обрабатываются существующим BrowserEngine, обход защиты отсутствует.
Количество проверяемых страниц и карточек ограничено; предупреждения отражают
неполноту выборки. Цены конвертирует только CurrencyService по RUB_KZT_RATE.

Исследовательский скрипт: `scripts/research_gifts.py`; сырые наблюдения находятся
локально в `.local/research/gifts`, не являются каталогом приложения.
# Final verification — 2026-09-18

Supplier-first discovery follows published `/catalog/…` links before classification. ArticlesData keeps exact variant identity and existing warehouse rules. Navigation/listing/product and 36-observation refresh observed. Brand/navigation-only branches remain unresolved, preventing COMPLETE. See `../provider-status.md` and `.local/final/quality.json`.
