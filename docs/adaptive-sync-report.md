# Adaptive background synchronization — отчет внедрения

Дата проверки: 24 сентября 2026. Расчет выполнен для фактических **28 001** offer в
рабочей PostgreSQL. Сервисы остановлены по запросу владельца, поэтому цифры «после» —
расчет планировщика на текущем составе индекса, а не выдуманное суточное live-наблюдение.

## До и после

| Метрика | До: почасовой обход | После: начальное COLD-состояние |
|---|---:|---:|
| Плановые полные catalog-запуски | до 144/сутки (6 поставщиков × 24) | 6/неделю, в среднем 0,86/сутки |
| Availability-запуски планировщика | до 144/сутки | coalesced batches по сроку tier |
| Detail navigations для availability | до 672 024/сутки | около 28 001/сутки |
| Detail navigations для reconciliation | до 672 024/сутки | около 4 000/сутки в среднем за неделю |
| Суммарные detail navigations | до 1 344 048/сутки | около 32 001/сутки + новые товары |
| Снижение detail-навигаций | — | примерно **97,6%** |
| Requests/product | не измерялось старым кодом | измеряется per job; зависит от adapter |
| Полезные изменения | не измерялись | `useful_change_rate` и unchanged dedup |
| Глубина очереди в снимке | 70 PENDING/RUNNING | существующая очередь сохранена; новые дубли coalesce |

Оценка трафика при условных 250 KiB на browser navigation: около 328 GiB/сутки до и
7,8 GiB/сутки после. Это модель, не счетчик wire bytes: размер страниц заметно различается,
а старые jobs не сохраняли объем ответа. Новые jobs сохраняют request/product/change-rate,
403 и 429; точный суточный трафик можно добавить только на HTTP/browser transport boundary.

## Новая модель

- `AVAILABILITY_REFRESH` обновляет цену и наличие без catalog traversal.
- `INCREMENTAL_CATALOG_SYNC` ежедневно читает listing/navigation и открывает detail только
  для неизвестных URL.
- `FULL_CATALOG_RECONCILIATION` еженедельно подтверждает полноту, удаления и структуру.
- `TARGETED_REFRESH` асинхронно обновляет ограниченный набор результатов пользователя.
- HOT обновляется через 2 часа, WARM через 6 часов, COLD через 24 часа. DORMANT не входит
  в обычный refresh и возвращается в работу после поиска, открытия или выбора.

Товар с устаревшим availability остается в результатах с `NEEDS_REFRESH`. Запрос пользователя
сначала получает индексный ответ; targeted refresh добавляется после сохранения результата и
не блокирует HTTP response. Fresh товар с подтвержденным недостаточным остатком по-прежнему
отсекается строгим quantity-фильтром.

Очередь объединяет повторные full и availability задания по поставщику, переносит новые URL в
существующий checkpoint и сохраняет максимальный приоритет. Разные `TARGETED_RESEARCH` query
имеют разные dedup keys. Повторный `PARSER_ANOMALY` остается заблокированным на той же версии
парсера и автоматически возобновляется после изменения `parser_version`. Повторные HTTP failures
получают exponential backoff; после порога supplier circuit временно открывается.

## Профили поставщиков

| Supplier | Лучший доказанный источник | Browser сейчас | Listing-level availability | Incremental механизм | Рекомендация |
|---|---|---|---|---|---|
| Gifts | variant structured data / `free_quantity` | да | не доказано | listing URL diff → новые detail | 2/6/24 ч, full 7 дней |
| Ucontay | InSales variant JSON | да | частично: identity | listing URL diff, embedded variants | 2/6/24 ч, full 7 дней |
| Portobello | Apollo `StocksDataLoader.free` | да | не доказано | paginator references → новые detail | 6/24 ч, full 7 дней |
| HappyGifts | detail DOM, central `Свободно` | да | нет | category pages → новые detail | 2/6/24 ч, full 7 дней |
| ArteGifts | configurator structured payload | да | не доказано | PAGEN/load-more URL diff | 6/24 ч, full 7 дней |
| Oasis | embedded warehouse data | да | не доказано | category URL diff | 6/24 ч, full 7 дней, backoff при HTTP |

Интервалы конфигурируются. Для поставщиков без доказанного дешевого availability endpoint
адаптивность достигается выбором offer и частоты; семантика складов не угадывается.

## Сохранность и проверка

Миграция `0014_adaptive_sync` аддитивна. До и после нее в базе было 174 jobs; SHA-256
канонического набора `id/supplier/kind/status/checkpoint/metrics` совпал:
`3aa4b099953fea80c11cc5f59609d2f143f18057a68382bf2c0a2c23188cf13b`.
Доказательство сохранено в `.local/final/adaptive-sync-migration.json`.

Проверки: backend **179 passed, 4 deselected**; отдельные adaptive/index regression tests
**33 passed**; Ruff, ESLint, TypeScript и Next production build прошли. Новые regression tests
покрывают coalescing с сохранением checkpoint, HOT/COLD scheduling, parser-version recovery и
circuit breaker/backoff.

## Ожидаемый эффект через неделю

После накопления usage-сигналов часто просматриваемые товары перейдут в HOT/WARM и получат более
частые точечные проверки. Основная масса останется COLD/DORMANT. Weekly reconciliation сохранит
контроль удаления и полноты каталога. Фактический эффект следует оценивать по семи полным суткам
метрик `products_per_minute`, `requests_per_minute`, `requests_per_product`,
`useful_change_rate`, queue wait и supplier 403/429. До такого окна нельзя честно заявлять
измеренное production-снижение трафика или точную среднюю задержку очереди.
