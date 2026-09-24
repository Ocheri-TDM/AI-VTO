# Research Index — Stage 4

Сайты остаются источниками фактов. Индекс — последние наблюдения, не master catalog и не обещание полного покрытия сайтов.

## Storage

- IndexedProduct: группа-кандидат модели, name, category, normalized search document, semantic tags.
- SupplierOffer: отдельный supplier ID, URL, variant metadata и стабильные поля Product. Offers не удаляются при группировке.
- ProductObservation: последнее price/stock/currency/fetched_at, expires_at, status, исходные коммерческие данные. История каждого изменения цены пока не ведётся.
- SupplierSyncState: independent due dates, lease, retry_after, failures и последняя ошибка.
- CatalogSyncJob: тип, priority, dedup_key, status, durable checkpoint и метрики.

Миграции `0004_research_index` и `0005_supplier_backoff` добавляют таблицы/индексы; Projects, Chats, Messages, ResearchSessions и history сохраняются. Legacy CachedProduct остаётся для совместимости.

В финальной реализации index ResearchSession хранит pool_offer_ids, indexed_offer_ids, pending_offer_ids и view/history. Полные Product DTO исключены из новых index snapshots и гидратируются из offers/observations. Старые live snapshots читаются без удаления. История хранит состояние view; цена берётся из latest observation, поэтому это не архив исторических цен. Исследование не удаляется по TTL.

## Query pipeline

Fast intent → IndexQueryService → SQL category/FTS + stock/freshness/price → ProductCategoryMatcher → color/material/attributes → полный pool → deterministic ranking/facets. LIMIT top-N отсутствует. API возвращает первую страницу до 50 товаров и matching_count; GET /api/researches/{id}/products выдаёт продолжение по cursor. Cursor конфликтует с изменённой view/порядком и возвращает 409.

Общий search document включает нормализованные имя, описание, материал, бренд, features и raw attributes. Известные категории используют B-tree category; dynamic terms — Russian FTS. Созданы GIN FTS, stock/price, expiry, supplier/product relations и queue indexes. На малом live индексе EXPLAIN ANALYZE выбирает Seq Scan: это нормально, forced-index benchmark не использовался. [PostgreSQL рекомендует GIN для полнотекстового поиска](https://www.postgresql.org/docs/current/textsearch-indexes.html).

## Freshness policy

Strict procurement допускает только FRESH observation, expires_at > now, известный stock ≥ requested quantity и подтверждённую конвертируемую цену. STALE определяется timestamp без удаления строки. REFRESHING/FAILED_REFRESH не доказывают наличие и исключаются из strict выдачи. UI сохраняет историю и предупреждает об устаревших данных. Цена UI — KZT; RUB_KZT_RATE остаётся configurable, локальное значение 5.2 не изменено.

Enrichment выполняется при ingest: нормализация, category, search document, RULE_BASED semantic tags. Эти теги не выдаются за свойства, подтверждённые поставщиком. Product факты не генерируются LLM. Grouping остаётся эвристикой возможной модели; все offers доступны.

Миграции 0006/0007 добавляют CatalogRun/CatalogSeen, lifecycle/missing_runs и TaxonomySuggestion, сохраняя пользовательские таблицы. Reconciliation требует полного безопасного discovery; новые background matches требуют явного принятия. SSE отправляет count/status/version deltas, без полного research DTO. Details скрывают служебные данные при DEBUG_AGENT=false. См. catalog-discovery.md и final-architecture.md.

## API and compatibility

Обычные `/api/chats/{id}/messages` и `/messages/stream` используют index-first. Параметр `legacy=true` сохраняет старый Stage 2 контракт для явно выбранного режима совместимости; это не default chat path. Research Workspace использует `/api/researches` и existing view/selection API. Новый независимый запрос в чате возвращает новый research ID. SSE возвращает состояние отдельно от жизни задания.

`POST /api/researches`, `/messages`, `/continue` и explicit refresh только читают индекс/ставят задания в очередь. Они не вызывают supplier methods. Недостаток материала отражается metadata_gap, пустой pool/metadata gap ставит targeted job. Manual refresh получает высокий priority. Длительные обновления отображаются одной activity-панелью вместо шести технических crawler logs.
