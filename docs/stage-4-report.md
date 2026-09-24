# Stage 4 — итог реализации, 17 сентября 2026

Рабочий проект расширен Background Research Index. Основной chat path читает PostgreSQL, а не ожидает supplier browser. Код, миграции, regression tests, реальные sync/refresh и HTTP/UI-проверки выполнены. Полного покрытия каталогов не заявляем: Oasis возвращает HTTP error, а discovery остальных источников пока частичная.

1. **Root cause.** Неизвестные «карандаши» попадали в общий CategoryDiscovery с default BOTTLE. Воспроизведено без модели/БД; тест сначала падал. Это backend intent bug, не frontend-фильтр.
2. **Isolation.** Новый предметный/ambiguous запрос создаёт новый intent/session. Исправлено наследование bottle при новом IT-запросе. Фоновое обновление старой сессии не меняет active research: выбор latest основан на created_at.
3. **Open taxonomy.** ProductTaxonomyNode и dynamic query concepts; отсутствие seed ID не запрещает поиск. Список начальных aliases расширен, но не является enum gate.
4. **Resolver.** UniversalCategoryResolver использует aliases/русские корни; неизвестный термин остаётся generic text query. Нет fallback в бутылки. Произвольное semantic equivalence пока не гарантируется.
5. **Expansion.** Категорийные aliases; у query есть origin/reason/confidence. Pencil не расширяется в bottle/pen. Свободная LLM expansion не используется без taxonomy guard.
6. **Relevance.** ProductCategoryMatcher проверяет название и supplier path/category; description-only mention не даёт EXACT. Футляры/наборы имеют guards. Strict pool принимает только EXACT.
7. **Index architecture.** Stable product/offer metadata отдельно от latest commercial observation; user ResearchSession отдельно от глобального индекса.
8. **IndexedProduct.** Candidate model group, normalized search document, category, rule-based semantic tags. Grouping эвристический.
9. **SupplierOffer.** Сохраняет каждый supplier listing/variant, URL и данные источника; grouping не удаляет offers.
10. **ProductObservation.** Последние цена/валюта/stock и observed/expires timestamps. Полная ценовая история вне текущей реализации.
11. **Freshness.** Target 60 минут. Strict query исключает stale/unknown/refresh-failed availability; история исследования остаётся. REFRESHING/FAILED_REFRESH явные, STALE вычисляется по времени.
12. **Scheduler.** Фоновый independent schedule источников: 600 секунд initial stagger, observation refresh 60 минут, discovery 24 часа; всё configurable.
13. **Jobs.** PostgreSQL queue, idempotent keys, priority, checkpoints, supplier lease, bounded backoff. Реальный concurrent claim test подтвердил единственного владельца и recovery expired lease.
14. **Discovery.** Supplier traversal через существующий contract, без product/page top-N. Возобновление по pending/visited/next URL. Начальные ветки основаны на taxonomy queries, не гарантирован полный supplier tree.
15. **Refresh.** Повторное чтение известных offers. Реально выполнено 17 сентября; timestamps вручную не продлевались. Ucontay продолжил незавершённый refresh и завершил его.
16. **Targeted research.** При пустом результате/metadata gap создаются jobs, chat сразу возвращает текущее состояние. Manual refresh — high priority. Jobs связываются с session, SSE обновляет pool.
17. **Шесть suppliers.** Все участвуют в queue architecture. Live attempts: Gifts/Ucontay/Portobello/HappyGifts дали observations; ArteGifts успешно прочитан при profiling, но короткий initial sync не успел наполнить его индекс; Oasis HTTP error. Нулевой refresh источника без offers не выдаётся за успешную discovery.
18. **Concurrency.** Global workers=2, supplier browser slots=1, page limit=2. DB lease защищает supplier между процессами; global worker limit относится к host.
19. **Browser reuse.** Один Chromium, supplier-scoped anonymous context/page reuse, idle eviction, CAPTCHA context закрывается. В профиле Gifts/Ucontay/HappyGifts/ArteGifts: один context и два reuse вместо трёх созданий.
20. **Batch/extraction.** Сохранены ArticlesData/structured JSON/network extraction. Ucontay один document возвращает несколько variants; refresh группирует family URLs. Не придумывались batch endpoints или stock из listing.
21. **Query engine.** SQL freshness/stock/price/category/FTS плюс validated color/material/attributes, далее весь matching pool. Простые запросы не требуют Qwen.
22. **DB indexes.** GIN Russian FTS, B-tree category, expiry, stock/price, offer relations, queue. EXPLAIN ANALYZE на реальных данных: execution 0.090 ms; небольшой индекс обоснованно сканируется Seq Scan.
23. **Chat без browser.** Обычные chat/research endpoints используют IndexedResearchService. Старый Stage 2 message contract доступен только через `legacy=true`. Acceptance выполнялся с незапущенным BrowserEngine и без worker.
24. **Qwen.** Существующая model abstraction сохранена для сложных refinements. Structured schema и action guards запрещают чужие product IDs и лишние side effects. Generation не применяется к каждой паре товаров.
25. **Ambiguous query.** IT-конференция порождает 10 relevant categories и query по индексу; без обязательного уточнения категории и synchronous crawling.
26. **Refinement.** Wood, price, premium, exclusions, similarity — локально. Материал/description/semantic tags используются как evidence; неизвестные факты не генерируются.
27. **Full pool.** Regression проверяет 61→61, ranking не отбрасывает хвост. UI pagination по 50 — только представление. «Покажи всё найденное» сбрасывает уточнения без browser.
28. **Lifecycle.** Research/history/selection не удаляются по TTL; indexed_offer_ids связывают результаты с индексом, snapshots сохраняют пользовательскую историю.
29. **API.** Index-first chat response использует Research DTO. Сохранены research/view/details/continue/cancel и SSE. Independent query возвращает новый ID; old compatibility API тестируется отдельно.
30. **UI.** Одна ненавязчивая панель обновления, freshness, полный matching count, фильтры/selection/reload. Supplier/stock/SKU не вынесены на карточки. Добавлены русские labels новых категорий.
31. **Migrations.** `0004_research_index`, `0005_supplier_backoff` применены. PostgreSQL migration head и pg_indexes проверены. Пользовательские таблицы/история не удалялись.
32. **Tests.** Новые index regression/integration tests: taxonomy/morphology, unknown query, 61 results, freshness, persistence, refinement, scheduler stagger, locking/recovery, CAPTCHA isolation, refresh, category isolation. Старые этапы остаются в общем прогоне.
33. **Live results.** После реального refresh HTTP вернул 20 карандашей, 36 ручек, 1 синюю бутылку, 8 IT-вариантов. Это matching pool имеющегося индекса, не весь каталог. Browser invocation в запросе: NO.
34. **До/после.** Stage 3 limited research ~190.1 s; Stage 4 index HTTP replay ~59–132 ms. Это разные lifecycle: crawl теперь отдельно, его время не исчезло.
35. **First useful response.** Карандаши 74.31 ms; wood refinement 99.17 ms; price 59.23 ms; ручки 61.36 ms; синие бутылки 61.10 ms; IT 131.88 ms. Единичные local measurements, не SLA.
36. **Known limits.** Частичный index coverage; Oasis HTTP failure; медленный ArteGifts; conservative taxonomy/semantic matching; нет embedding implementation; latest-only observations; JSON session snapshots и полные SSE payloads; большой каталог требует отдельного load test. Автоматическое удаление исчезнувшего товара по одному failed request не выполняется. Presentation/auth/image generation не добавлялись.

## Документация и артефакты

Финальный полный прогон: **116 passed, 4 opt-in tests deselected**. Ruff, ESLint и Next production build с TypeScript прошли. Для последних изменений metadata/checkpoint отдельно повторены index tests.

- [Background execution/config](background-research.md)
- [Research Index и API](research-index.md)
- [Taxonomy/root cause](search-taxonomy.md)
- [Performance, provider tables, ограничения измерений](performance-stage4.md)
- `.local/stage4/live.json`, `api.json`, `provider-profile.json`, `refresh.json`, `refresh-ucontay.json`, `storage.json`
- `.local/stage4/ui`: Chromium screenshots desktop/mobile, checks.json без page errors.
