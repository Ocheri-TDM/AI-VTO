# Stage 4: measurements, 16–17 September 2026

## Baseline

В Stage 3 реальные acceptance runs с бюджетом 180 секунд занимали около 190.1 секунды; continuation IT-конференции — 180.5 секунды. Это время ограниченного live research, не доказанный полный обход и не «точно 5–10 минут». Артефакты `.local/research/acceptance` сохранены.

В Stage 4 разбор понятной категории и local index query отделены от sync. Первый реальный sync 18 supplier/query jobs использовал 25 секунд на job, без обрезания количества товаров. Собрано 150 observations, включая неподходящие по stock/color: индекс хранит данные для будущих запросов, а strict query выполняет constraints отдельно.

## Initial index acceptance

Chromium был закрыт перед измерением запросов. Данные реальных поставщиков, не fixtures.

| Запрос | Candidates | Valid | Parse + query, ms | Browser in query |
|---|---:|---:|---:|---|
| Карандаши 300шт | 21 | 21 | 12.70 | NO |
| Ручки 300шт | 36 | 36 | 9.57 | NO |
| Синие бутылки 300шт | 28 | 1 | 6.27 | NO |
| Синий мерч для IT конференции 300шт | 64 | 8 | 72.09 | NO |
| Антистресс 100шт — dynamic concept | 0 | 0 | 6.70 | NO |

Это все matching offers данного свежего индекса, не все товары сайтов. Количество меняется при refresh и expiry. `.local/stage4/live.json` содержит intents, names, job cursors, errors и SQL plan. Первый HTTP replay выявил отдельную ошибку классификации нового IT-запроса как follow-up; исправлено, добавлен regression. Финальный HTTP replay — `.local/stage4/api.json`.

## Provider profile with context reuse

Финальный HTTP replay после настоящего refresh 17 сентября (ASGI HTTP, worker отключён, BrowserEngine не запущен):

| Запрос | Pool / показано | HTTP response, ms | Browser |
|---|---:|---:|---|
| Карандаши 300шт | 20 / 20 | 74.31 | NO |
| Только деревянные | 20 / 9 | 99.17 | NO |
| До 2000 тенге | 20 / 9 | 59.23 | NO |
| Ручки 300шт | 36 / 36 | 61.36 | NO |
| Синие бутылки 300шт | 1 / 1 | 61.10 | NO |
| Синий мерч для IT конференции 300шт | 8 / 8 | 131.88 | NO |

Ucontay refresh завершён продолжением: 221 observation updates / 46 detail reads суммарно; 128.17 секунды за два запуска. Число updates включает повторные variants, это не размер уникального пула. Grouping family URLs добавлен между запусками; нельзя считать это чистым A/B benchmark.

Chromium UI проверил карандаши, wood/price refinements, reload, отсутствие bottle leakage и overflow на 1440×900 и 390×844. Скриншоты `.local/stage4/ui`, page_errors пуст.

Один search, первая карточка и следующая страница, где доступны. Значения в миллисекундах; нет данных означает отсутствие соответствующей страницы/ошибку, а не нулевую latency.

| Supplier | Search | Detail | Pagination | Network requests | Created / reused contexts |
|---|---:|---:|---:|---:|---:|
| Gifts | 912 | 401 | 664 | 181 | 1 / 2 |
| Ucontay | 1095 | 1368 | 3487 | 416 | 1 / 2 |
| Portobello | 1035 | — | — | 116 | 1 / 0 |
| HappyGifts | 2780 | 1393 | 1447 | 493 | 1 / 2 |
| ArteGifts | 12812 | 13784 | 15013 | 163 | 1 / 2 |
| Oasis | HTTP error | — | — | 1 | 1 / 0 |

Portobello не вернул pencil URL в этом профиле. Oasis не был успешно просканирован. Повторные context creations сокращены с одного на чтение до одного на цепочку из трёх чтений; это не контролируемый network-speed A/B тест. Ucontay structured document даёт несколько variants одним чтением; refresh дополняет это группировкой одинаковых family URLs. Gifts/Portobello используют существующие JSON/network extraction; наличие точного stock в listing не предполагалось без проверки.

## PostgreSQL

Реальный EXPLAIN ANALYZE pencil+stock+freshness на маленьком индексе: Execution Time 0.090 ms, Planning Time 0.463 ms. План использовал Seq Scan + Hash Join, а не принудительно выбранный index. GIN FTS и B-tree indexes установлены и проверены через pg_indexes; для большого каталога нужен отдельный нагрузочный прогон.

## Tests and limits

Regression suite включает исходные этапы и новые index tests: 61→61 без top-N, morphology/open concept, supplier category guard, freshness, local wood/price refinement, persistence, scheduler staggering, lease/recovery, supplier isolation, real background refresh. Legacy chat tests явно выбирают compatibility mode.

Данные вчерашнего sync естественно стали stale на следующий день. Затем выполнен реальный Observation Refresh, без изменения timestamps вручную. `.local/stage4/refresh.json` и `refresh-ucontay.json` фиксируют длительность и результат. Нулевой refresh источника без известных offers не означает успешную discovery этого источника.

Ограничения: не весь каталог исследован; Oasis HTTP failure; ArteGifts существенно медленнее других; initial discovery опирается на seed/category queries; semantic matching консервативен; fresh completeness не гарантируется за час при недоступном источнике; SSE пока snapshots, не deltas; latency benchmarks единичные локальные измерения, не production SLA.
