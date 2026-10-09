# Availability audit addition ? 23 ???????? 2026

??????????????? observation ?????? ???????? ?????? `available_now` (??????????? API-???? `stock_quantity`/DB `stock`), `incoming`, `reserved`, `total`, `incoming_at`, normalized statuses, bounded `source_availability` ? `parser_version`. `NULL` ????????, ??? supplier ?? ??????? ????????; ?? ?? ?????????? ?????. Incoming ??????? ????????????? ?? ?????????? ??????? ????????.

Quantity query ?? ????????? ????????? ??? ???? ?????????? ??????: `AVAILABLE_NOW` ???? `PARTIALLY_INCOMING`/`INCOMING_ONLY`. ????? ??????? ? ???????? ?????? `AVAILABLE_NOW_ONLY` ? ????????? ????????? incoming. ??? quantity availability ?? ????????? catalog product. ???????? ??????? ?????????? ?????? ???????????? ????????: `N ????????`, `N ???????? ? M ? ????` ??? `M ? ????`.

## Mapping ????? suppliers

| Supplier | ?????????? available now | Incoming/reserved/date | Parser/evidence | Live validation |
|---|---|---|---|---|
| Gifts | `variant.free_quantity`, ????????? ??????? ??? reserve | ????????? incoming/date ?? ????????, ??????????? `NULL` | provider normalization + bounded source label, `availability-v1` | free quantity ????? ????????; incoming NO |
| Ucontay | variant `quantity`, ??????????? ?????? ?????????? | warehouse/pending ???? ?? ???????????; incoming mapping ?? ??????? | InSales variant JSON, bounded warehouse fields, `availability-v1` | available YES; incoming NO |
| Portobello | `StocksDataLoader.free` ?????? storage type `MAIN`, ???????? ?????? ?????? | REMOTE/reserve/incoming ?? ????????????; date ?? ???????? | Apollo structured response, `availability-v1` | available YES; incoming NO |
| ArteGifts | `available` ?????????? variant, ???????? ?????? store | incoming/reserved/date ?? ???????? | configurator structured payload, `availability-v1` | available YES; incoming NO |
| Oasis | exact variant Moscow free stock; reserve/transit/fabric ????????? | ???? transit/reserve ?? ????????? incoming ??? ?????????? semantics | embedded product warehouse data, `availability-v1` | ??????? live supplier HTTP failure; NO ??? ?????? ??????? |
| HappyGifts | `???????? N ??` ? tooltip ???????????? ?????? | `? ????/?????????/??????????? N ??` ??????????? ????????, ???? ??????? ????????????; date ???? ?? ??????????? | bounded central warehouse text, `happygifts-availability-v2` | free tooltip contract YES; incoming parser contract implemented, live occurrence ???? ?? ???????????? |

????, ????????????? ? supplier, ?? ???????? ? ???????? `NULL`. Multiple incoming shipments ?? ??????????????: ??????? ?????????????? ????????? ?? ???? ???????????? evidence ??? ????????? `IncomingShipment` table.

## HappyGifts root cause ? DB audit

?? re-observation: 546 canonical products, 554 offers, 554 observations; `available_now`: 243 NULL, 6 zero, 305 positive; `incoming`: 554 NULL, 0 zero, 0 positive; price: 554 positive; status column: 554 FRESH. `FRESH` status ??? ?? ???? ?? ???????? ???????? `expires_at` ??? procurement.

?????? ??? quantity ?? ????????? availability/freshness predicate, ??????? NULL stock ?? ??? ???????? ?????????? HappyGifts ?? ?????? ??????. ??? ???????? ??????? index ????????? ???? HappyGifts match; grouping ??? ?? ??????. ???????? ?????? HappyGifts ? partial pagination/catalog coverage ? ?????????? category-specific records ? active index. ??? ???????? ?????? ???????? ? ?????????? ???????? ???????? `happygifts:616175292` ??? quantity, ???? ??? unknown stock. ??? quantity ??????????? available now ????????? ?? ???????????? ?????.

HappyGifts parser v2 ???????? ????????? free ? incoming ?? ???????????? supplier text, ????????? bounded raw evidence ? ?? ??????? ??????? ?? total/warehouse. ????? migration ????????? priority-100 `OBSERVATION_REFRESH`; ????????? observation worker lane ????????????? starvation ?? ??????? ??????? discovery jobs. Job ID `2d3924ff-900d-4847-b739-27c11cba0138`.

## Acceptance

Fixtures A?E ????????????: default `300 ??` ?????????? available A ? incoming options B/C/D, ????????? ????????????? E; `300 ??, ?????? ? ???????` ?????????? ?????? A; ?????? ??? quantity ?????????? A?E. Migration `0009_availability` ????????? ?? fresh PostgreSQL (19 tables). API suite: **163 passed, 4 deselected**; Ruff, ESLint, TypeScript ? Next build passed.

??????????? ???????? ?????: ?????? live re-observation HappyGifts ??? ???????????, incoming-positive live sample ? ??????????? ????????? HappyGifts ???????? ? ???? ??????? ?? ????????. COMPLETE coverage ? ??????? HappyGifts ?? ??????????.

---

# ??????????? active catalog ? regression ????????, 22 ???????? 2026

## ?????????????? ???????

?? ????????? ? ??????? PostgreSQL ??????????????? baseline ???????? 32 `IndexedProduct` ? 32 `SupplierOffer`, ??? ????????? ????????? ????????? ????? `??????`; 14 ?????????? ???? fresh. `ResearchPlanner` ????????? ???????? ???????? ??? `quantity=None`, `categories=["accessory"]`, ?? SQL ???????? ????????? taxonomy-????????? ??? gate. ?????? dynamic-????????? ???????? lexical FTS condition. ??????? `IndexQueryService` ????? 5 ??????????. Grouping ??? 5?5, stale-filter ??? ??????? ??? quantity ??? ?? ??????????: ?? dedup, ?? freshness ?? ???? ???????? ??????.

??????????? ?????: SQL ?????? recall ??? `canonical taxonomy OR Russian FTS OR indexed literal stem evidence`, ? `ProductCategoryMatcher` ????????? ?????? supplier text evidence ? ??? ????????? taxonomy concepts. ??????????? URL ??? ??????? ??? ??????? ???. GIN `pg_trgm` ?????? ???????? migration `0008_search_trigram` ??? literal fallback.

????? ??????????? ??????? ?????????????? index ?????? 31 ?????: Gifts 26, ArteGifts 3, HappyGifts 1, Ucontay 1, Portobello 0, Oasis 0. SQL ??? 37 ??????????; 6 unrelated accessory ???? ????????? ?????????; canonical grouping 31?31; browser ?? ?????????. ?????????? ?????? ?????? ? 365.72 ms. ????????: `.local/final/keychains-audit.json`. ??? 31 ? ???? matching pool ???????? index, ? ?? ?????????????? ??????? ???????? ????????????.

## Active snapshot ? background sync

???????????? `CatalogRun` + `CatalogSeen` ???????????? ??? ?????????? staging-generation. ?? ????? PARTIAL run ?????? `SupplierOffer` ???????? searchable; ?????????? ????? ?????? ????? ????????????? ??????????????, ?? ?????????? ?????? ?? ???????? removal evidence. ?????? COMPLETE run ? ???????????? roots/branches/pagination ???????? anomaly validation ? reconciliation. `MASS_REMOVAL`, `STOCK_COLLAPSE`, `PRICE_COLLAPSE`, `CURRENCY_CHANGE` ? `PARSE_FAILURE` ????????? run ? `QUARANTINED`; ??????? observations ? offers ???????? ?????????. ??????????? delete ??????? ???.

Scheduler ?????? ????????? ?????? `CATALOG_DISCOVERY` ??????? supplier ??? ? 1 ??? ????? ????????? COMPLETE, ?? stagger 300 ??????. ????????????? traversal ?? ???????????: stable dedup key, supplier lease ? persisted cursor ?????????? ??? ?? job. ????? ????????? ??????????? API ???????? ?? ?????? index ? scheduler ??? UI ??????? Gifts discovery ? `RUNNING`; ???????????????? ????? ????????? ????????. ????????? jobs ????? ? ??????? ?? stagger. `MAX_PAGES_PER_QUERY` ? `MAX_PRODUCTS_PER_QUERY` ?? ???????????? background full discovery; ??? ????????? ? legacy user search. Full discovery ??????? observed roots, children and `next_url` ?? `pagination_exhausted`.

?????? discovery ?????? ??????????? bounded batches ? checkpoint ?????? ????? ???????? ??????????. ??? crash ???ublished URLs ??????????? ????????????. Synthetic provider ? 7 ?????????? (30?6 + 13) ??? 193 offers; ????????? sync ???????? 193, ?? 386.

Stable catalog metadata ???????? ?????????? ?? ???????? stock observation. Quantity-less ?????? ?? ???????? freshness predicate. Explicit quantity ??????? `FRESH`, ?????????? observation ? `stock >= quantity`. ??????? Research Workspace ?????? ?? ?????????? stale blocker ? ?? ?????????? ???????????? ??????????? crawler; ???????? ???????????? ?????? ??????????????? ???????? ??????????. Developer diagnostics ???????????.

## ??????????? ????????? ???? ? live coverage

?? ?????? ?????????? ??????? ???? ???????? 123,295,411 bytes. ??????? counts: ArteGifts 1,018 products / 1,187 offers; Gifts 2,531 / 2,531; HappyGifts 545 / 554; Oasis 56 / 56; Portobello 62 / 155; Ucontay 5,537 / 5,537. ??? counts ????????? ??????????? partial runs ? ?? ???????? COMPLETE coverage.

????? ??????????? live multi-page evidence: ArteGifts ? 56 families, 3 pages; HappyGifts ? 25 families, 2 pages; Portobello ? 36 families / 129 offers, 2 pages; Ucontay supplier search ? 36 + 21 cards across 2 pages. Gifts ????? ??????????? real `a.ctlg-pages-arrow.next` `/page2`; Oasis ? ???????? `a.pagination__btn` ? `/page-2`. ??? Gifts ? Oasis ? ???? ??????? ?? ???????? ?????????? first?middle?last, ??????? ?? coverage ???????? PARTIAL. ?????? ?????????????? live pagination acceptance ?? ??????????.

## ???????? ????? ???????????

- API: **162 passed, 4 deselected**. Deselected live tests ?? ????????? ??? passed.
- Ruff: passed.
- ESLint: passed.
- TypeScript/Next production build: passed; routes `/`, `/_not-found`, `/dev/index` built.
- Migration `0008_search_trigram` ????????? ? ??????? PostgreSQL.
- Regression: older/missing canonical classification + known taxonomy still resolves through lexical evidence.
- Pagination/idempotency: 7 pages ? 193 unique offers; repeat ? 193.
- Runtime: API health succeeded; scheduler started automatically; Gifts sync entered RUNNING; index search stayed available.

???????????: ??? ????? supplier discovery runs ?????? ?? ????? ??????????? COMPLETE coverage; ??????? ???????? ?????????? ?????? HTTP/browser/parser failures, ? ?????? initial traversal ?? ????????. ??????? initial full-sync duration, hourly completed duration ? six-supplier last-page evidence ??????????? ? ?? ??????????? fixture ??? synthetic benchmark. Oasis ????????? ??????? HTTP failure state. ??????? ????????? production-ready ??? index-first ?????? ? ??????????? ???????? ??????????, ?? ?? ??? ????????? ???????????????? ???? ????????? ????????.

---

# Final Research Platform — технический аудит, 22 сентября 2026

## Исправление zero-result targeted research: «фляжки»

### Root cause

Проблема воспроизведена на основной PostgreSQL до изменения кода. Запрос `фляжка`,
`фляжки`, `фляжек`, `flyazhka`, `flyazhki` не находил ни одной записи: карточки
HappyGifts из `/catalog/posuda/flyazhki/` в индексе не было. Resolver сохранял
запрос как отдельный dynamic concept, но детерминированный parser ошибочно задавал
`quantity=1`, хотя пользователь не указывал количество. Затем
`IndexRepository.candidates()` для любого запроса безусловно требовал одновременно
`FRESH`, `stock >= 1` и известную цену. После четырёх дней offline это исключало
весь stale index, включая товары, для которых требовался только поиск стабильной
карточки.

Zero-result path создавал шесть `TARGETED_RESEARCH` jobs с priority 10. Refresh
имел priority 20, в базе уже находились jobs с priority 30, а оба worker могли быть
заняты длительным catalog traversal. Targeted job использовал общий `discover()`:
он открывал все product URLs и всю пагинацию вместо короткого supplier search.
Зафиксированный старый HappyGifts job открыл 264 product pages, накопил около
969.8 s только на product pages и 1544.3 s суммарной работы. CatalogGraph содержал
ветку «Фляжки», но запрос её не использовал. Причина 20-минутного ожидания —
комбинация неправильного implicit quantity/freshness filter, низкого приоритета,
starvation и полного traversal в targeted path.

### Исправленное поведение

- Отсутствующее количество теперь остаётся `None`. Fresh stock проверяется только
  при явном quantity constraint. Stale/unknown stock не скрывает стабильную карточку;
  API продолжает помечать её `STALE` и не заявляет наличие.
- Dynamic matching использует имя, supplier category и breadcrumbs. Общий русский
  stemmer покрывает формы `фляжка/фляжки/фляжек/фляжку/фляжкой` и аналогичные редкие
  категории без специального URL или flask-only business rule.
- CatalogGraph выполняет fuzzy stem lookup до supplier search. В основной базе он
  находит наблюдавшиеся ветки HappyGifts, Gifts и ArteGifts. Если graph miss, adapter
  использует реально работающий supplier-native search. HappyGifts сохраняет
  наблюдённую category route из результата для следующего запроса.
- User targeted job имеет priority 100; manual refresh — 60, stale refresh — 2,
  routine discovery — 0. Есть отдельный interactive worker lane. Длинные discovery
  и refresh уступают выполнение на безопасном checkpoint, если ожидает более
  приоритетная работа; supplier lease и транзакционная граница сохраняются.
- Targeted execution имеет per-supplier timeout 20 s, сохраняет первый продукт
  немедленно, затем пишет небольшими batches. Первый supplier result не ждёт пять
  остальных. При исходно пустом pool `sync()` сразу добавляет первый match в session;
  для непустого пользовательского pool сохраняется прежний new-matches workflow.
- API различает `INDEX_RESULTS`, `TARGETED_SEARCHING`, `PARTIAL_RESULTS`,
  `NO_RESULTS_YET`, `COMPLETED_NO_MATCHES`, `SUPPLIER_ERROR`.

### Live evidence и timings

Реальная страница `https://happygifts.ru/catalog/posuda/flyazhki/` 22 сентября 2026
вернула одну карточку: «Фляжка MONROE, 235мл», variant id `616175292`, изображение,
цену 629 RUB, материал и размеры. Подтверждённый свободный stock на странице не был
извлечён, поэтому `stock=None`; это корректно для запроса без тиража.

На свежей изолированной PostgreSQL базе: index query + enqueue 353.20 ms, queue
31.01 ms, первый UI-доступный продукт 8334.37 ms, targeted completion 8395.24 ms.
Желаемая цель 5 s на холодном Chromium не достигнута; результат всё же появляется
за секунды, а не за 20 минут. Повторный запрос занял 21.28 ms, вернул один товар из
PostgreSQL, `browser_invoked=false`, targeted jobs не создавались. После live run
category route `/catalog/posuda/flyazhki/` появилась в graph lookup.

Основная база до исправления не содержала flask. Live targeted run записал
`happygifts:616175292` в global index и ResearchSession; повторный main-index запрос
занял 37.01 ms без BrowserEngine. Полные raw results находятся в
`.local/final/targeted-flasks.json` и `.local/final/targeted-flasks-main.json`.

Добавленные метрики: `index_query_ms`, `targeted_job_queue_ms`,
`targeted_job_start_ms`, `supplier_targeted_search_ms`,
`time_to_first_product_ms`, `time_to_first_result_event_ms`, `targeted_total_ms`,
а также per supplier `category_graph_hit`, `supplier_search_used`, `pages_opened`,
`products_seen`, `products_matched`.

Изменены `ai/basic.py`, `database/index.py`, `domain/taxonomy.py`,
`domain/search_state.py`, `application/index_research.py`,
`application/background_index.py`, `application/research.py`, `api/research.py`,
provider contract и HappyGifts adapter. Добавлены regression tests и live script
`scripts/check_targeted_flasks.py`. Нового Stage, schema migration, embeddings,
auth или UI redesign нет.

Изменения внесены в существующий проект, миграции применены, PostgreSQL/Ollama/API/production frontend запущены. Основной индексный сценарий и пользовательские уточнения проверены. **Полное выполнение Definition of Done не заявляется**: публичные каталоги не исчерпаны, большой matching pool всё ещё материализуется в Python, нейросемантический слой не включён после отрицательного сравнительного eval. Ниже различаются реализованный код, live evidence, synthetic проверки и незавершённые требования.

## Проверяемое состояние поставщиков

Снимок `.local/final/quality.json` после реального discovery/refresh. Это количество offer observations в текущем индексе, не размеры каталогов. Freshness меняется со временем; running workers продолжают обновления.

| Источник | Offers | Fresh | Stale | Pending branches | Failed branches | Discovery |
|---|---:|---:|---:|---:|---:|---|
| Oasis | 26 | 0 | 26 | 123 | 123 | PARTIAL |
| Ucontay | 3,746 | 0 | 3,746 | 240 | 20 | PARTIAL |
| Gifts | 1,340 | 0 | 1,340 | 351 | 4 | PARTIAL |
| Portobello | 126 | 0 | 126 | 16 | 2 | FAILED |
| HappyGifts | 391 | 0 | 391 | 158 | 2 | PARTIAL |
| ArteGifts | 285 | 0 | 285 | 150 | 3 | PARTIAL |

Это повторный снимок 22 сентября после четырёх дней без процессов: все commercial observations честно стали STALE; они не были продлены искусственно. Background refresh начинается после запуска worker. FAILED у Portobello относится к persisted discovery job, а не ко всем запросам адаптера: последний live sample после исправления paginator прошёл. Failed branches входят в pending, это не два независимых множества. Ни одному источнику здесь не присвоен COMPLETE.

## Аудит по 55 пунктам спецификации

1. **Final architecture.** Supplier websites → supplier-first background traversal → stable product/offer index + latest commercial observations → PostgreSQL/category/attribute retrieval → guarded orchestrator → paginated Research Workspace. Browser отсутствует в обычном индексном request path. [Архитектура](final-architecture.md).

2. **Изменённые компоненты.** SupplierProvider contract получил catalog navigation; добавлены domain/catalog, providers/catalog_navigation, database/catalog, application/index_quality и API diagnostics. Расширены BackgroundIndex, IndexRepository, ResearchRepository, intent/planner/refinement, public Research API/SSE, frontend hooks/workspace/details и developer dashboard. Рабочие adapters и Stage 1–4 не переписаны.

3. **Schema/migrations.** `0006_catalog_safety`: CatalogRun, CatalogSeen, lifecycle/missing_runs у SupplierOffer. `0007_taxonomy_research`: ResearchRecord.created_at + индекс и TaxonomySuggestion. Existing IndexedProduct, SupplierOffer, ProductObservation, jobs и user tables сохранены. Новых внешних DB/queue нет.

4. **Supplier discovery architecture.** Published roots → navigation-derived branches → existing provider pagination → exact product refs → extraction → core normalization → index. Начальные catalog jobs больше не используют список taxonomy queries. Targeted research сохраняет query-based назначение.

5. **Реальный mechanism каждого supplier.** Gifts: опубликованные `/catalog/…`, ArticlesData, точные variants. Ucontay: `/collection/…`, listing next, InSales family/variant JSON. Portobello: `/catalog/…`, Angular paginator и nomenclature references. HappyGifts: `/catalog/…`, next-page DOM, active color. ArteGifts: `/catalog/…`, PAGEN_1/load-more и configurator active SKU. Oasis: `/categories/…`, page navigation и product data. URL берутся с опубликованных страниц. [Подробности](provider-status.md), `providers/*.md`.

6. **Oasis diagnosis.** Первый обычный Chromium home request: HTTP403, без redirect, HTML, `403 Forbidden`, около237 ms. Позже другой обычный anonymous context прочитал roots/listing/product. Доказано intermittent access denial; серверная причина неизвестна. Protection не обходилась, parser fix не выдуман.

7. **Catalog coverage.** Состояние в таблице выше. Known published roots — наблюдаемая часть навигации, не доказательство наличия всех возможных публичных коллекций. Navigation-only/configurator branches могут оставаться unresolved. Full exhaustion live не достигнуто.

8. **Discovery counts.** Текущий снимок содержит 5,914 offers суммарно; он включает ранее существовавшие observations и новые background discoveries. Короткие execution slices не выдаются за обнаружение всех5,914. Fixture с пятью supplier branches подтвердил discovery всех400 продуктов, включая40 UNKNOWN, независимо от taxonomy.

9. **COMPLETE/PARTIAL.** COMPLETE требует roots_observed, непустые branches, COMPLETE/exhausted у каждой, отсутствия pending/failed URLs и unresolved errors. Успешный возврат worker недостаточен. TIME_BUDGET/COVERAGE_LIMITED отображаются PARTIAL; supplier failures отдельно. [Discovery](catalog-discovery.md).

10. **Resume/checkpoint.** Persistent job checkpoint хранит roots, parent routes, branches, listing/next URL, page, pending/visited/failed refs, reported totals и run_id. Commit выполняется после extraction и смены cursor. При timeout медленная ветка переносится в конец очереди с сохранением cursor, чтобы не блокировать каждый slice. HappyGifts/ArteGifts больше не обнуляют published route; старые checkpoints восстанавливают его только из уже наблюдавшегося root graph. COMPLETE дополнительно требует присутствия каждого root в branch graph.

11. **Restart test.** Настоящий Python worker process завершён принудительно; другой process восстановил persisted checkpoint и продолжил с product10, а не с начала. Результат20 unique offers. Изолированный PostgreSQL, supplier fixture, lease expiry ускорен явно. Это не live supplier process-kill test.

12. **Reconciliation.** Только safe COMPLETE catalog run увеличивает missing_runs: NOT_SEEN → SUSPECTED_REMOVED → REMOVED после трёх отсутствий. Seen возвращает ACTIVE. Partial/error run не удаляет offers. Физического удаления исторических offers нет. Definitive404 removal отдельным новым механизмом не реализовано.

13. **Anomaly protection.** MASS_REMOVAL, STOCK_COLLAPSE, PRICE_COLLAPSE, CURRENCY_CHANGE, PARSE_FAILURE сравниваются с предыдущими observations. Existing изменения staged; подозрительный complete run quarantined. Safe partial slice публикует положительные/обычные observations, но удерживает positive→zero stock до aggregate validation. New offers searchable сразу. Автоматического review/approve quarantine UI нет.

14. **Index quality.** IndexQualityService считает offers total/active, missing facts/images/categories, unmapped labels, failed/pending branches, roots, latest jobs/errors/anomalies и timestamps. Отчёт доступен только при DEBUG_AGENT. На больших индексах текущая реализация считает часть агрегатов после materialization.

15. **Freshness.** Commercial target60 минут; latest observation timestamps не продлевались вручную. Strict procurement исключает stale/unknown/unacceptable stock. Реальный time-budgeted refresh обработал Gifts36 и Ucontay152 observations; оба задания остались PENDING, не объявлены завершёнными. Stable index и research не исчезают по TTL.

16. **Taxonomy.** Сохранена открытая ProductTaxonomyNode architecture, aliases и supplier mappings. Classification выполняется после discovery. Неизвестный concept остаётся generic text query; нет default BOTTLE.

17. **Unknown categories.** Supplier labels/breadcrumbs сохраняются и входят в search document. TaxonomySuggestion хранит supplier/path, bounded samples, mapping/confidence/status PENDING_REVIEW. Qwen не может самостоятельно переписать taxonomy. UNKNOWN не препятствует persistence/search.

18. **Search document.** Name/category/path/breadcrumbs/description/material/colors/dimensions/capacity/brand/attributes/keywords нормализуются в core. Semantic representation bounded2400 символами. Raw HTML и supplier internals не используются как meaningful semantic text.

19. **Russian lexical search.** PostgreSQL Russian FTS плюс deterministic aliases/корни и category evidence. Regression dataset проверяет pencil/pen/bottle/backpack/umbrella/thermos/powerbank; добавлен alias зарядка. Название/категория важнее случайного упоминания товара в description.

20. **Semantic enrichment.** Существующие extracted facts и RULE_BASED tags используются для premium/minimal/business/sport/eco/tech/classic/creative preferences. Inferred style не выдаётся за supplier material/stock/price. Это ограниченный rule-based semantic layer, не общий multilingual neural retrieval.

21. **Embedding model.** Реально локально загружена и вызвана `embeddinggemma` через Ollama,768 dimensions. Paid API не использовалась. В production query path embeddings не активированы.

22. **Почему модель.** Проверен локальный multilingual кандидат, доступный через существующий Ollama. Сравнительный labelled eval показал macro precision@2: rules≈0.917 против vectors≈0.667. На этой маленькой выборке нет evidence выигрыша; условное внедрение embeddings отклонено. Результат не доказывает непригодность всех моделей.

23. **Dimensions/version.** Эксперимент768 dimensions; corpus/labels versionv1 в eval script. Production embedding storage/version/reindex и pgvector migration **не реализованы**, поскольку кандидат не включён. EmbeddingProvider interface сохранён. Vector similarity поэтому не заявляется.

24. **Hybrid retrieval.** Реализовано сочетание SQL hard constraints, Russian lexical/category evidence, structured attributes и rule-based preference ranking. Neural hybrid SQL+vector отсутствует. Hard constraints проверяются до soft ordering.

25. **Similarity.** Только реальные продукты существующего pool: category, color, facts lexical overlap и price proximity. Команда ordinal/selected IDs проверяется server-side. Embedding similarity отсутствует; это явно ограничение относительно полного neural similarity требования.

26. **Hard/soft.** Quantity/category/budget и «только материал» — hard. «Желательно материал», premium/tech/minimal/cheap — soft reorder. Color family tolerant, strict wording сужает оттенки. Initial hard constraints остаются при reset/undo; optional refinements сбрасываются командой «покажи всё».

27. **Qwen role.** Существующая Qwen3:4b Q4_K_M остаётся bounded structured decision layer для сложных команд. Простые команды выполняются fast path. Live model check вернул LOCAL_RERANK/premium для «выглядят дороже своей цены»; `.local/final/qwen.log`.

28. **Model guards.** Typed decisions, закрытые actions, product ID scope, SELECT-only per-category semantics, price include/exclude repair/fallback. Model не получает SQL/shell/arbitrary browser/HTTP. Context bounded representative products и recent messages; тысячи DTO не отправляются модели.

29. **Full result pool.** Retrieval/ranking не делают top-N truncation. Регрессии61→61 и live fixture62→50+12. Synthetic large test сохранил12,500 matching offers. Shortlist — отдельное пользовательское состояние, не уничтожение base pool.

30. **Pagination.** GET `/api/researches/{id}/products?cursor=&limit=50`, максимум100. Cursor tied к view version + ordered identity fingerprint; изменение view возвращает409. First response50 products и matching_count. Cursor пока offset/fingerprint поверх materialized pool, не SQL keyset.

31. **SSE.** `research.updated` содержит counts/status/version/freshness fingerprints; `new_matches.available` — count. Полный session на каждый event больше не передаётся. Client делает bounded reload. Иллюстративный synthetic delta113 bytes; это не измерение всего SSE transport.

32. **Session/history scalability.** Indexed sessions persist IDs/view/history вместо product DTO JSON. Legacy snapshots читаются. SQL latest research использует created_at, фоновое обновление старой сессии не меняет active binding. Synthetic12,500 pool session≈340KB. Hydration всего pool и JSON ID lists остаются scalability debt.

33. **New matches.** Sync вычисляет pending IDs без автоматического изменения pool. Пользователь принимает notice через `/new-matches`, merge сохраняет filters/selection. Unit/integration fixture и **реальный HTTP/background acceptance** подтвердили workflow:82base offers,1новый acceptable match, принятие83offers без сброса state. `.local/final/live-new-matches.json`. Это новый подходящий вариант вследствие live обновления, не доказательство открытия ранее неизвестного supplier product.

34. **Facets.** Реальные category/material/brand/capacity/color/public attributes/style tags, без пустых facets и служебных SKU/stock полей. Count scan по eligible materialized pool. Отдельный availability facet как переключатель strict/stale policy не добавлен.

35. **Desktop UI.** Existing dark visual language сохранён. Cards, full matching count, server pages, selection, filters, details, new-match notice. Playwright1920/1440/1280 без horizontal overflow/page errors. Часть supplier images загружается медленно/недоступна; placeholder сохраняет layout.

36. **Mobile UI.** 768/430/390 проверены; readable grid, AI chat dialog, Escape close, одна основная колонка. Public material/dimensions/capacity/brand показаны в details, если известны. Неизвестные факты не заполняются.

37. **Developer dashboard.** `/dev/index` и `/api/dev/index`, DEBUG_AGENT gate. Supplier counts/freshness/coverage/errors, expandable roots/branches/jobs/anomalies. Нет новой auth; debug включать только локально.

38. **Supplier failure.** Independent supplier leases/jobs, bounded retry/backoff, parser quarantine; старые observations/history сохраняются. Ошибка одного provider не должна остановить worker host или chat. Tests проверяют failure isolation; live failed/partial sources сосуществуют с работающим index chat.

39. **CAPTCHA.** Existing no-bypass policy сохранена. CAPTCHA branch/job классифицируется, context закрывается, retry не чаще часа. Остальные suppliers продолжают. CAPTCHA isolation regression проходит; CAPTCHA не обходилась в live checks.

40. **Performance p50/p95.** Последний real-observation E2E:10 операций29.98–87.85ms, p50≈61.21ms; single replay, не SLA. Synthetic10k/25k: p50 query487.16ms, sample maximum969.85ms (8 samples, слабая оценкаp95). Warmed max589.22ms — target<500ms не доказан. [Performance](performance-final.md).

41. **Large-index results.** 10,000 IndexedProducts,25,000 offers,12,500 pool, first response22,810 bytes/50cards, facets+response111.84ms, save19.96ms, price filter46.50ms, similarity124.93ms. Extra query tracemalloc peak114.43MB Python allocations, не RSS. 50k/100k не выполнен.

42. **Semantic eval.** Reproducible12-product labelled synthetic corpus,6 query families, precision@k, recall@k, expected labels и списки false positives. Macro precision/recall: rules≈0.917, vectors≈0.667; vectors проиграли на eco/tech/creative. Raw `.local/final/semantic-eval.json`. Это не live semantic quality или статистически широкий eval. Hard-constraint regression:0 violations, поскольку semantic ordering получает только eligible pool. Strict stock отдельно гарантирует один подтверждённый склад: несколько warehouse rows никогда не суммируются; используется консервативный максимум одной валидной `free/available` записи.

43. **Query acceptance.** Real observations: IT300 dark-blue9; pencils30037; wood15; price≤2000 сохраняет15. Pencil intent отдельный, без inherited bottle/color/budget. Pen и другие literal intents покрыты dataset; Stage4 live pen counts не выдаются за новые final measurements.

44. **Provider contract tests.** Все6 публикуют typed capabilities: discovery, identity, pagination, variants, price, currency, stock, images, category path и refresh. Все имеют deterministic published-URL/navigation checks и live navigation/listing/product sample. Rich Oasis/Ucontay/Gifts fixtures сохранены; ArteGifts/HappyGifts/Portobello теперь имеют pure normalization contracts для identity/variants/price/currency/stock/images. Portobello regression исключает unrelated Apollo offers и REMOTE stock; multiwarehouse tests запрещают суммирование. Shared worker tests покрывают resume/idempotency/error classification. DOM fixture depth всё ещё различается между adapters.

45. **Live supplier tests.** Все6 product samples наблюдались; Arte/Port потребовали longer/repeated attempts. Oasis intermittent403. Port final sample OBSERVED после active paginator fix. Эти успешные samples не означают все branches/variants supported. Raw reports `.local/final/provider-live*.json`.

46. **E2E dialogue.** IT9 → budget9 → tech9 reordered → without bottles4 → similar4 → undo4 → show all9. Pencil37 → wood15 →≤2000:15. Every acceptance response browser_invoked=false в application с незапущенным BrowserEngine, использующим реальный PostgreSQL. `.local/final/dialogues.json`.

47. **Restart/resume result.** Fixture process-kill success в `restart.json`. Дополнительно настоящий ArteGifts Chromium worker был завершён во время traversal: новый owner продолжил тот же job/run_id с133roots;80/80 offer IDs остались уникальными, status честно PENDING/TIME_BUDGET. Lease ускорен только после подтверждённого завершения прежнего owner. `.local/final/arte-live-restart.json`. Full catalog completion не доказан.

48. **Migration verification.** Existing database upgraded to0007. Финальный fresh isolated `souvenir_migration_85a49e0537` upgraded from empty: head0007,19tables, success. `migrations.json`. User tables/history сохранены, pgvector extension не требовалась.

49. **Final test count.** Последний полный прогон после targeted-search fix: **160 passed, 4 deselected, 44.23 seconds**, включая stale-without-quantity, rare-category morphology, CatalogGraph acceleration, interactive priority и streaming into an empty session. `.local/final/tests-final.txt`. Deselected opt-in tests не считаются passed.

50. **Ruff/lint/typecheck/build.** Ruff application/tests, ESLint, TypeScript noEmit, Next production build выполнены; `.local/final/{ruff,lint,typecheck,build}.txt`. Browser UI checks отдельные от build. Не заявляется CI/cloud deployment.

51. **Dead code removed.** Удалён taxonomy-seeded default catalog discovery и obsolete README future-stage обещания. Исправлен whole-SSR Port listing. Explicit legacy API сохранён: references/tests подтверждают consumers, поэтому его не удаляли вслепую.

52. **Remaining technical debt.** Full-pool Python hydration/ranking/facets; JSON membership; full staged run payload retention; quarantine manual review отсутствует; complete publication/reconciliation не одна общая транзакция; navigation-only branches; limited generic semantic matching. Эти ограничения остаются в текущем продукте, не перенесены в выдуманный следующий Stage.

53. **External supplier limitations.** Intermittent access errors/Oasis403; slow Arte; changing DOM/SSR; supplier warehouse semantics; unknown quantity metadata; partial public navigation. COMPLETE зависит от реально исчерпанных observed public branches, commercial availability — от acceptable fresh observation.

54. **Windows startup.** Команды ниже используют уже подготовленные portable PostgreSQL/Ollama в этой workspace; fresh checkout требует установки зависимостей/БД по README. API поднимается до crawl. Один API process включает in-process scheduler/workers; debug dashboard optional.

55. **Readiness assessment.** Запускаемый локальный Research Workspace с проверенным index-first chat, typed contracts всех6 providers, persistent background queue/checkpoints, safe reconciliation, public pagination, local refinement/undo, live new-matches и live supplier restart/resume workflows. **Не production-ready в смысле всей финальной спецификации**: нет доказанной полноты каталогов и принятого neural semantic retrieval; большой pool имеет известные CPU/memory ограничения. Эти обязательные gaps открыто зафиксированы, полного завершения проекта не объявляем.

## Точные команды запуска Windows

Из корня проекта в PowerShell:

```powershell
.venv/Scripts/python.exe scripts/local_db.py start
.venv/Scripts/python.exe scripts/local_llm.py
$env:PYTHONPATH='apps/api'
$env:BACKGROUND_RESEARCH_ENABLED='true'
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
.venv/Scripts/python.exe -m app
```

В другом терминале:

```powershell
npm.cmd --prefix apps/web run build
npm.cmd --prefix apps/web run start
```

UI: http://127.0.0.1:3000. Для локальной диагностики задать `$env:DEBUG_AGENT='true'` перед запуском API и открыть `/dev/index`. API docs: http://127.0.0.1:8000/docs. Не запускать несколько локальных API workers ради ускорения supplier crawling.

## Артефакты

`.local/final`: tests-final.txt, ruff.txt, lint.txt, typecheck.txt, build.txt, quality.json, dialogues.json, live-new-matches.json, migrations.json, restart.json, large-index.json, semantic-eval.json, provider-live*.json, catalogs/, ui/. Реальный refresh: `.local/stage4/refresh-gifts-ucontay.json`, `.local/final/refresh-acceptance.log`. Arte slices: artegifts-resume.log. Синтетические benchmark/restart базы отделены от пользовательского индекса.

## Normalized procurement availability (2026-09-23)

Availability is now stored as multiple source-preserving records per supplier offer in
`availability_observations`. Each row retains location, state, total/free/reserved quantities,
ETA or lead time, observation time, parser version, confidence, and bounded source evidence.
Migration `0010_availability_records` is additive and does not reinterpret existing ambiguous
single-stock rows as free stock. Existing catalog, offer, research, history, and selection data
remain intact.

`ProcurementAvailabilityService` derives `available_now`, confirmed incoming, remote availability,
earliest ETA, fulfillment flags, and confidence. Only `ON_HAND.free_quantity` proves current stock.
`total_quantity` never substitutes for free quantity; `NULL` remains unknown; incoming and remote
records remain separate. Strict quantity search uses fresh normalized records and returns explicit
tiers (`AVAILABLE_NOW`, `AVAILABLE_WITH_INCOMING`, and, only for an explicit wait intent,
`REMOTE_OPTION`). A query without quantity keeps catalog membership independent of availability.

Provider extraction remains the authority for source semantics. The shared helper emits ON_HAND and
INCOMING records only for fields a provider already normalized. The current observed mappings are:

| Supplier | Current source mapping | Additional states proven in current parser |
|---|---|---|
| Gifts | variant free quantity | none |
| Ucontay | supplier `available` quantity and retained warehouse evidence | none promoted |
| HappyGifts | central warehouse `Свободно` | incoming text when explicitly present |
| Portobello | local `free`; remote rows excluded from current stock | none promoted |
| ArteGifts | selected variant/store available quantity | none promoted |
| Oasis | Moscow variant availability excluding reserve/transit/factory | none promoted |

The developer index report now includes normalized observation totals, fresh/stale counts, known and
unknown current stock, incoming/remote/preorder counts, and parser versions. Observation refresh has
a dedicated worker lane; after deployment refresh jobs were queued for all six suppliers, with
HappyGifts priority raised first. Search continues to use the last published index while these jobs
run.

Verification on this revision: migration of the existing PostgreSQL database reached
`0010_availability_records`; backend tests passed **167** with **4 opt-in tests deselected**; Ruff,
ESLint, TypeScript, and the production Next.js build passed. The normalized table initially contains
zero rows by design and is populated by re-observation rather than unsafe legacy backfill. During
verification the first **54 Gifts records** had been published; HappyGifts and Ucontay refresh jobs
were running and the remaining supplier refreshes were queued or continuing from checkpoints.
Therefore a completed live six-supplier site/database/UI comparison is not claimed here.
## User accounts and supplier source links (2026-09-23)

Migration `0011_user_accounts` extends the existing `users` table with a case-folded unique name,
Argon2id password hash, update time, and last-login time. Opaque 256-bit browser tokens are stored
only as SHA-256 token identifiers in `auth_sessions`; the browser cookie is HttpOnly, SameSite=Lax,
path-scoped to `/`, and configurable as Secure (disabled only for the local HTTP target). Passwords
are never returned, logged, or placed in browser storage. Register authorizes immediately; logout
deletes the server-side session and expires the cookie.

Chats now carry a non-null `user_id`; projects already had ownership. Research sessions, messages,
history, and selections inherit ownership through their chat/project foreign-key chain. API ownership
checks run before reads and mutations and deliberately return 404 for another user's identifiers.
The supplier index, offers, observations, scheduler, and workers remain global. Existing projects and
chats were assigned to a migration-created `__legacy_local__` account whose sentinel hash cannot be
used to log in, preserving all prior data without exposing it to newly registered accounts.

Supplier product links continue to originate from `SupplierOffer.source_url`. The normalization
boundary accepts only HTTP(S), rejects credentials and fragments, and permits only the configured
supplier's domain or its subdomains. No product URL is synthesized. Research list DTOs still omit
supplier internals; the detail DTO exposes `source_url` and an `offers` array. Evidence-based
canonical groups preserve every real supplier URL. The existing `.dialog-body` renders one readable
external action or one action per supplier, always with `target="_blank"` and
`rel="noopener noreferrer"`; missing or rejected URLs produce no button.

Live acceptance registered two users, confirmed owner access `200`, cross-user access `404`, and an
index-only “брелки” research with **72 matches**. Detail checks returned direct supplier product URLs;
the browser acceptance opened an ArteGifts product URL from the existing dialog on desktop and
mobile. Captures: `docs/source-link-desktop.png` and `docs/source-link-mobile.png`.

Final verification: PostgreSQL is at `0011_user_accounts`; **172 tests passed** and **4 explicit
opt-in tests were deselected**. Ruff, ESLint, TypeScript, and the Next.js production build passed.
## Open-vocabulary product understanding (2026-09-23)

Search membership is now driven by PostgreSQL evidence and catalog language rather than a closed
category enum. Migration `0012_open_product_concepts` adds a dynamically populated concept registry
and offer associations. Supplier category titles and every breadcrumb node are normalized, retained
as original labels, added to the weighted search document, and linked to the observed offers.
The existing database was rebuilt from persisted navigation evidence without crawling or deleting
offers: **301 concepts**, **21,386 concept/offer links**, and **23,988 offers** were processed.

The search document weights the product name by repetition, then catalog category/breadcrumb
evidence, structured facts, attributes, colors, capacity, and public keywords. Unknown concepts
remain searchable immediately. A regression fixture introduces “Умные багажные метки” with no code
mapping and retrieves it through the index. Compound names are split into independent evidence, so
“брелок-открывалка” matches either concept. The query layer combines Russian FTS, normalized lexical
terms, bounded catalog synonym seeds, and indexed trigram matching on product names. Migration
`0013_product_name_trigram` adds the dedicated GIN trigram index. Fuzzy matching is only candidate
evidence; the weighted matcher must still confirm the product name or supplier navigation path.

Concrete searches no longer inherit every product from a broad canonical parent. This fixed the
observed `шопперы → all bags` and `сумки-холодильники → all bags` false expansion. The matcher does
not use incidental description text for exact membership. Broad/ambiguous intents may use multiple
planner categories, while hard filters remain separate. Ranking records the matched concept and
match type without overwriting the product's actual category and never truncates the logical pool.

The reproducible real-PostgreSQL evaluation is stored in `docs/search-eval-real.json`. It ran 57
literal queries against the working index; 53 returned matches. Selected verified pools were:

| Query | Matches | Manual sample result |
|---|---:|---|
| ручки | 2,926 | pen products across beginning, middle, and tail |
| бутылки | 1,965 | bottle products across beginning, middle, and tail |
| шопперы | 202 | shopping/tote bags, no generic laptop-bag expansion in sampled rows |
| сумки-холодильники | 54 | subtype pool after broad-parent rejection |
| брелоки | 66 | keychains including valid compound subtypes |
| фляжки | 14 | flask products; sampled pool contained no bottles |
| powerbank | 80 | Russian external-battery names matched from the synonym seed |

Median evaluation latency was **1,376.3 ms** and p95 was **1,655.2 ms** on the warmed local database.
The p95 is slightly above the 1.5-second engineering target and remains an optimization item for very
large pools; it is no longer the earlier unindexed 2.6–3.4-second document-similarity scan. The four
zero-result queries were `мыши`, `значки`, `диффузоры`, and `медали`. They are reported as current
index/discovery gaps and are not silently replaced with unrelated categories.

Validation: **174 tests passed**, including unknown-category discovery, compound concepts, typo
tolerance, broad-parent false-positive prevention, full-pool behavior, availability constraints,
accounts, and source URLs. Four opt-in tests were deselected and are not counted as passed. Ruff,
ESLint, TypeScript, and the production Next.js build passed.

## Availability pipeline correction (2026-09-24)

The live HappyGifts audit found the concrete parser defect: the page contains duplicate
`.avilability-tabs-item` elements, and the adapter selected the first element whose label contained
`Центральный`. On some products that element was only the tab caption, while a later element held
`Свободно N шт.` and `В резерве M шт.`. This produced an unknown current quantity and discarded the
reserve. The adapter now collects the unique availability blocks and selects the most informative
central block. It persists `free`, `reserved`, and the supplier-evidenced total separately. A live
product check returned **22,379 free**, **1,001 reserved**, and **23,380 total** with parser version
`happygifts-availability-v3`. `В пути` is a separate `INCOMING` record; an ETA is retained even when
the page does not publish a shipment quantity. Unknown quantity remains `NULL`, never zero.

The generic observation boundary now also preserves total and reserved quantities in normalized
`AvailabilityRecord` rows. Procurement derives current, incoming, remote, and alternative supply
separately. Search without a quantity does not reject a catalog product for unknown/stale stock;
strict quantity membership remains based on acceptable current free stock. API list and detail DTOs
now expose the normalized availability view, and the product card/detail dialog render that view
without interpreting supplier text or presenting an unknown quantity as zero.

Portobello's optional brand fields no longer use unchecked dictionary indexing, and required offer
name / paginator fields now fail with the controlled `SUPPLIER_PARSING_ERROR` classification instead
of an unclassified `KeyError`. Browser HTTP failures now log the actual status and redirect flag.
A live Oasis root navigation on this machine succeeded and ended at the same HTTPS URL with the
expected catalog title; this check does not claim that every Oasis catalog branch is complete.

The queue audit before the scheduling fix showed six pending discovery jobs and a large targeted
research backlog. The worker host now reserves one execution lane for `CATALOG_DISCOVERY`; targeted
research and observation refresh retain dedicated lanes, supplier leases still prohibit concurrent
driving of one adapter, and the global browser/page semaphores remain unchanged. After restart the
Gifts discovery checkpoint advanced immediately while interactive jobs remained queued/running,
demonstrating that discovery is no longer starved.

Database baseline during this audit contained **6,612 normalized availability observations**:
ArteGifts 1,775, Gifts 1,233, HappyGifts 2, Oasis 8, Portobello 156, and Ucontay 3,438. Those historical
rows mostly use older parsers and do not retroactively gain reserve/ETA evidence; corrected values
arrive through normal refresh. Final validation: **175 tests passed**, four explicit opt-in tests were
deselected, Ruff and ESLint passed, and the Next.js TypeScript production build passed. Live supplier
coverage is still whatever each checkpoint proves; no incomplete traversal is reported as complete.

## Persistent composite conversation and availability trace (2026-09-26)

Composite results now use the existing project Chat/Message chain. User and assistant messages are
stored in PostgreSQL with a HardRequest revision reference. `Project.active_query_id` restores the
current Composite result, messages and cases after F5 and after an API restart. The AI Research
composer remains visible in Case View. Follow-ups create a new persistent HardRequest revision;
the prior revision and its cases remain available. Rebuild also creates a revision rather than
deleting the current cases first. Migration `0017_composite_conversation` adds only revision and
message-reference columns.

The false-negative availability root cause was a `limit=180` applied before final category,
color and availability filtering. Depending on row order, the bounded pool contained stale offers
while refreshed offers for the same category remained outside it. Composite retrieval now evaluates
the complete SQL category pool. `ProcurementEvaluation` records the exact pass/reject reason and
source records. UNKNOWN, stale, total-only and reserved-only evidence still fails strict quantity
requests.

Live red `>=50` funnel: umbrellas 985 raw / 838 category / 84 red / 41 with records / 11 fresh /
4 quantity-confirmed / 4 eligible; backpacks 1265 / 979 / 88 / 51 / 1 / 1 / 1; socks 511 / 7 /
1 / 0 / 0 / 0 / 0; T-shirts 16 / 10 / 0 / 0 / 0 / 0 / 0. The result remains 0/50 because socks
and T-shirts are genuine bottlenecks, while availability propagation is proven by the passing
umbrella and backpack offers.

Position-specific parsing produced BLUE umbrellas, BLACK raincoats, WHITE socks and RED T-shirts,
all with global quantity 250. Live eligible counts were 6, 0, 0 and 0. Follow-up “Футболки сделай
черными” changed only the T-shirt position in revision 2. “Теперь от 50 штук” changed the global
quantity in revision 3. Eight conversation messages and the active third revision survived F5 and
a FastAPI restart. Playwright reported textarea restored, pagination `0`, no page errors and no
horizontal overflow. Artifacts: `.local/composite-availability-audit.json`,
`.local/composite-conversation-live-v2.json`, `.local/composite-reload-e2e.json`,
`.local/composite-restart-e2e.json`, and `.local/final/ui/composite-conversation-reload.png`.

Validation: **187 tests passed**, four opt-in tests deselected. Ruff, ESLint, TypeScript and the
Next.js production build passed.

## Composite low-capacity forensic correction (2026-09-26)

The live query “50 sets: blue umbrellas, pencils, pens and mugs, minimum 20” initially exposed two
different bottlenecks. The genitive `кружек` did not resolve to the existing `mug` taxonomy node,
reducing mug retrieval to two rows; adding the observed grammatical form to the shared resolver
restored 883 raw / 882 category / 133 blue mug offers. The remaining low capacity is commercial
freshness coverage: pencils have 35 blue offers but one fresh confirmed offer, mugs 133/1, and pens
457/2. Umbrellas increased from 9 to 21 confirmed offers while background refresh was running.

Final Test A funnel: umbrellas 1003 raw, 838 category, 194 blue, 90 with normalized availability,
42 fresh, 63 with free>=20, 21 fresh free>=20, 21 eligible; pencils 342/240/35/13/1/12/1/1;
pens 3098/2966/457/167/2/141/2/2; mugs 883/882/133/110/2/88/1/1. Capacity is therefore one unique
complete case. SQL retrieval used the complete logical pool; retrieval took 10,368 ms and filtering,
deduplication and assembly 1,241 ms in the measured run.

Current normalized availability coverage by supplier: ArteGifts 3840/3983 offers (96.41%, 52 fresh),
Gifts 3720/7282 (51.08%, 53 fresh), HappyGifts 533/2178 (24.47%, 101 fresh), Oasis 43/80
(53.75%, 8 fresh), Portobello 584/708 (82.49%, 0 fresh), and Ucontay 7174/23688 (30.29%, 61 fresh).
Forty real BLUE offers with fresh `ON_HAND.free_quantity>=20` were passed through
`ProcurementAvailabilityService`; all forty returned `CURRENT_FREE_SUFFICIENT`.

Composite now queues bounded `TARGETED_REFRESH` jobs only for missing, stale or unknown evidence.
The budget is configurable through `composite_refresh_safety_factor` and
`composite_refresh_max_per_position`; explicit insufficient free stock does not trigger recovery.
Jobs use the existing supplier leases, provider cooldowns and deduplication. For this live run three
jobs were durably queued, but Ucontay, Oasis and Portobello were still held by persisted provider
retry/circuit states, so no post-refresh capacity increase is claimed. The initial 1/50 result remains
available and the UI reports that availability checking is pending. SSE follows the active revision
and switches Case View when a recovery revision is published.

The availability table is treated as a snapshot set. A successful refresh with no normalized records
now deletes the previous set rather than attaching an old stock record to a fresh ProductObservation.
Simple/Composite BLUE membership was comparable: Simple pools were umbrellas 194, pencils 35, pens
474 and mugs 142; Composite pre-availability color pools were 194, 35, 457 and 133. Variant stock is
hydrated by the same SupplierOffer ID whose structured color passed the hard constraint; a multi-offer
family audit and regression prevent borrowing stock from another color variant.

The current meaning of `50 sets` remains 50 distinct suggestion cases. `minimum 20` is the minimum
confirmed supplier quantity for each selected product; it is not interpreted as procurement of 50
physical identical kits. This existing contract was not changed silently.

Artifacts: `.local/composite-blue20-live-v3.json`, `.local/composite-forensic-db.json`,
`.local/composite-simple-parity.json`, and `.local/composite-blue20-recovery-v3.json`. Validation:
**190 tests passed**, four opt-in tests deselected; Ruff, ESLint, TypeScript and the production Next.js
build passed.

## Project query history and Hard Request workspace (2026-09-25)

Projects now retain an explicit `ProjectQuery` history. Creating a new primary query keeps the same
project, archives the previous query, creates an independent `ResearchSession`, and updates
`Project.active_query_id`. Opening an archived query only reads its saved session; activation is a
separate operation. Migration `0015_hard_requests` backfilled the latest existing research session
for each project without changing supplier jobs or catalog checkpoints.

Hard Request is stored as a separate project artifact with normalized request, case, position, and
candidate rows. One schema-validated local-model call may interpret the request; explicit category
labels and numeric constraints from the source text remain authoritative, with a deterministic
fallback. Assembly reads bounded pools from the existing index and creates at most three candidates
per position without a Cartesian product. Availability uses tiers A (fresh on-hand), B (on-hand plus
confirmed incoming), C (remote/preorder/factory/production), and D (unknown or stale). Total and
reserved quantities never establish availability.

Live acceptance used the request “Найди-собери 50 наборов состоящих из 5 категорий: ручки,
карандаши, панамы, рюкзаки, бутылки. От 250 шт.” The persisted plan retained all five labels,
`target_sets=50`, `quantity.minimum=250`, `candidates_per_position=3`, and `BASE_COLORS`. The current
index produced 50 cases, 250 positions, 750 candidate placements, and 470 distinct indexed products.
The current availability evidence classified 3 placements as A and 747 as D. This high D count is
reported as uncertainty rather than stock. Selecting an alternate candidate persisted the selected
and `user_locked` flags across a new API read. Chromium rendered 10 cases on the first page, five
positions and 15 lightweight candidate controls in the first case, with no page errors or horizontal
overflow. Captures are `.local/final/ui/hard-request-toolbar.png` and
`.local/final/ui/hard-request-cases.png`; raw API evidence is
`.local/hard-request-acceptance.json`.

Validation after these changes: backend **182 passed, 4 opt-in tests deselected**; Ruff, ESLint,
TypeScript, and the Next.js production build passed. PostgreSQL is at `0015_hard_requests`.

## Unified request and strict composite correction (2026-09-25)

The separate Hard Request and New Query entry buttons were removed. The ordinary Research input now
parses one request and chooses SIMPLE for one resolved category or COMPOSITE for two or more resolved
categories. SIMPLE keeps the product grid; COMPOSITE uses the existing Case View. History remains
secondary navigation and previous sessions are preserved.

The previous composite assembler admitted every category match, sorted unknown availability last,
and rotated through the same pool until it produced the requested case count. It did not apply the
parsed color at all. Consequently non-blue and stale/unknown candidates appeared in cases marked
COMPLETE, and products repeated between cases. The corrected eligibility boundary runs before
assembly: explicit blue accepts only the normalized blue family; UNKNOWN color is rejected; an
explicit quantity accepts only fresh A/B/C procurement evidence. Stale, unknown, zero-free,
reserve-only and total-only observations are rejected. Alternative supply requires explicit quantity
evidence rather than the mere presence of a preorder/factory label.

Assembly now deduplicates by `IndexedProduct` identity, allocates every selected product globally
before alternatives, and uses each remaining alternative at most once. It stops at the actual
bottleneck instead of repeating products. Migration `0016_composite_policy` marks all cases from the
older parser/assembler policy as `STALE_ASSEMBLY`; the catalog and discovery checkpoints are not
changed.

Live request: “Собери 50 наборов из зонтов, дождевиков, носков, футболок от 250 шт синего цвета”.
Diagnostics on the current index were:

| Category | Raw | Category match | Blue | Quantity-confirmed | Eligible unique |
|---|---:|---:|---:|---:|---:|
| зонты | 180 | 154 | 38 | 0 | 0 |
| дождевики | 126 | 86 | 22 | 0 | 0 |
| носки | 180 | 2 | 1 | 0 | 0 |
| футболки | 16 | 10 | 1 | 0 | 0 |

Requested cases: 50. Theoretical and actual maximum unique complete cases: 0. Assembled cases: 0.
Duplicate selected products: 0; invalid-color final candidates: 0; unknown-availability final
candidates: 0. Returning 0/50 is intentional because the current observations do not prove that any
blue candidate in every requested category can satisfy 250 units. A SIMPLE request, “синие
термокружки”, returned the normal grid with 87 indexed products through the same endpoint.

Playwright verified that the separate Hard Request and New Query buttons are absent, the unified
input opens the strict Composite view, 0/50 is rendered without candidate cards, and the same input
returns to a 50-card SIMPLE page. There were no page errors or horizontal overflow. Captures:
`.local/final/ui/composite-strict-zero.png` and `.local/final/ui/unified-simple-grid.png`; raw live
evidence: `.local/composite-blue-live.json`.

## Composite retrieval and targeted recovery optimization (2026-10-02)

The 10.3-second Composite candidate path was caused by repeated full offer hydration for every
position and PostgreSQL sequential scans over JSON color payloads. `EXPLAIN (ANALYZE, BUFFERS)`
captured execution times of 1.30–1.96 seconds per position before the change. Composite retrieval
now performs indexed candidate-ID selection, one bulk product/availability hydration, and bounded
parallel position lookup. Explicit colors are applied to the concrete `SupplierOffer` before
hydration and are still rechecked by `ColorNormalizer`. Migration `0019_offer_normalized_colors`
adds an incrementally maintained JSONB color projection and GIN index; normalized catalog and
availability tables remain authoritative.

The saved plans in `.local/composite-sql-profile/` now measure 2.68 ms for pencils, 10.59 ms for
mugs, 13.81 ms for umbrellas, and 365.75 ms for the high-cardinality pen pool. The repeatable
10-run benchmark hydrated 1,200 unique offers instead of roughly 8,300 repeated DTO rows and used
six SQL statements instead of eight. Its warmed p50 was 1,185.91 ms, p95 1,333.80 ms, and maximum
1,312.07 ms. A live HTTP run while background workers were active recorded 942.69 ms retrieval,
88.69 ms filtering/assembly, and 1,031.38 ms total after intent parsing. The same HTTP request took
37.93 seconds end to end because local Qwen structured parsing dominated; this latency is reported
separately. An in-process bounded cache now reuses validated intent for an identical normalized
query without caching availability.

Targeted recovery previously reevaluated a Composite request when products were only staged, before
`CatalogSafety` published the normalized observations. The reevaluation therefore saw the old
snapshot. `TARGETED_REFRESH` now publishes first and triggers one subscribed reevaluation at the
safe slice/completion boundary. Recoverable candidates are ordered by position bottleneck and remain
limited to missing, unknown, or stale evidence. The live blue `umbrellas / pencils / pens / mugs >=20`
request advanced from 0 to 2 complete unique cases after Ucontay completed its refresh, creating
persistent revision 2. Oasis remained in cooldown with `TEMPORARILY_UNAVAILABLE`; Portobello remained
in cooldown with `SUPPLIER_PARSING_ERROR`; other suppliers continued. The recovered revision reports
zero invalid colors, zero unknown-availability candidates, and zero duplicate selections.

The second live position-color request preserved BLUE umbrellas, BLACK raincoats, WHITE socks, and
RED shirts with quantity 250 as independent hard constraints. Its initial capacity was zero because
fresh confirmed procurement was absent; no wrong-color or unknown-availability product was accepted.
Playwright reload acceptance retained the Case View and messages with no page errors or horizontal
overflow. Validation on this revision: 191 backend tests passed and four opt-in tests were deselected.
Ruff passed for the application, tests, migrations,
and new diagnostics. ESLint, TypeScript, and the Next.js production build passed.

## Query Understanding and typo normalization (2026-10-03)

The universal request path now runs one shared `QueryUnderstandingService` before both SIMPLE and
COMPOSITE intent parsing. It preserves original text, normalizes Unicode and separators, resolves
Russian morphology, safe phonetic forms, keyboard-layout/confusable characters, transliteration and
fuzzy catalog terms, then passes validated normalized text into the existing parsers and retrieval.
Vocabulary comes from the open `ProductConcept` registry and existing taxonomy aliases. It is
bounded, versioned and cached for five minutes; lookup uses exact, phonetic, transliteration and
token-length buckets instead of scanning the complete vocabulary for every token. Brand, model and
SKU-like tokens remain protected.

The previous `корондаш -> 0` behavior occurred because the universal endpoint sent literal text to
the SIMPLE planner. It produced a dynamic literal concept and could enqueue targeted research before
spelling recovery. Live acceptance now persisted:

```json
{
  "original_query": "корондаш",
  "normalized_query": "карандаш",
  "concepts": ["pencil"],
  "confidence": 0.96,
  "resolver": "MORPHOLOGY",
  "llm_used": false,
  "mode": "SIMPLE"
}
```

The current index returned one matching pencil offer in 322.53 ms end to end. Normalization took
7.14 ms, used no browser and created zero catalog jobs. Original and normalized values are stored
separately on the research intent and `ProjectQuery`; migration `0020_query_understanding` adds the
durable project-query fields. Playwright reload retained the original `корондаш` heading and the same
result before and after F5, without page errors or horizontal overflow.

Required typo families resolved as follows: `корондаш`, `карондаш`, `карандаж`, `карандашы` and
`крандаш` to pencil; `ручька`/`рючка` to pen; `крушка`/`кружек` to mug; `зонтик`/`зонтоф` to
umbrella; `рюкзаг`/`рюкзкоф` to backpack; `фудболка`/`футболак` to tshirt; `насоки`/`наскофф` to
socks; and `даждевик`/`дождевек` to raincoat. `karandash`, `ryukzak` and `zont` used
transliteration. Mixed safe confusables and `термо кружка`/`термо-кружка` are covered. Parker,
Parker Jotter, Blackie 3D, Powerbank and USB-C stayed unchanged; unknown `квантосборник` was not
rewritten. Deterministic parsing covers `250шт`, `250 штук`, `от 250`, `не меньше 250`,
`минимум 250` and `>=250`; negated colors become exclusions instead of required colors.

The live composite typo request `Сабери 50 набороф из синих зонтоф, чорных дождевеков, белых
наскофф, красных футболак от 250 штук` resolved to 50 target cases with umbrella/BLUE,
raincoat/BLACK, socks/WHITE and tshirt/RED, preserving minimum quantity 250. Deterministic
normalization took 42.28 ms. Since this is complex, Qwen was used once (21,180.16 ms); its output
was schema-validated while deterministic category, color and number evidence remained authoritative.
Current strict availability yielded zero complete cases because required observations were stale or
absent; no invalid-color or UNKNOWN-availability product was admitted.

The deterministic benchmark contains 300 executions across 30 representative queries: cold catalog
vocabulary load 99.56 ms, warm p50 6.41 ms, p95 12.78 ms and maximum 14.90 ms. Resolver counts were
210 morphology, 50 exact, 10 fuzzy and 30 transliteration, with zero LLM fallbacks and browser
invocations. The generated mutation corpus has 153 cases; 145 resolved to the expected concept
(94.77%). Ambiguous aggressive mutations are deliberately left unchanged. Although `pg_trgm` is
installed, this portable PostgreSQL returned an empty `show_trgm` result for Cyrillic text. The
measured bounded vocabulary load used 432 rows and took 8.483 ms, so the cached bucketed resolver was
retained instead of claiming a nonfunctional Cyrillic trigram index.

Artifacts: `.local/query-understanding-live.json`, `.local/query-understanding-ui.json`,
`.local/query-understanding-benchmark.json`, `.local/query-understanding-regression.json`,
`.local/query-understanding-sql.json`, and `.local/final/ui/query-understanding-reload.png`.
Final validation: **240 backend tests passed**, four explicit opt-in tests were deselected; Ruff,
ESLint, TypeScript and the Next.js production build passed. PostgreSQL is at
`0020_query_understanding (head)`.

## Deterministic-first Composite Query Understanding (2026-10-03)

Composite no longer invokes Qwen merely because two or more categories were resolved. The shared
normalizer and deterministic composite grammar now run first, followed by an explicit
`IntentCompleteness` check covering category resolution, constraint binding, numeric safety,
negation scope, unresolved spans, and confidence. Qwen is used only for a machine-readable reason
such as `SEMANTIC_PREFERENCE`, `AMBIGUOUS_CONCEPT`, or `CONVERSATIONAL_REFERENCE`.

The live current-database query `Сабери 50 набороф из синих зонтоф, чорных дождевеков, белых
наскофф, красных футболак от 250 штук` completed Query Understanding in **149.56 ms**, with
`llm_used=false`, `target_cases=50`, `minimum_quantity=250`, and umbrella BLUE, raincoat BLACK,
socks WHITE, and tshirt RED positions. Browser invocations and background jobs were zero. A separate
semantic request used Qwen with reason `SEMANTIC_PREFERENCE` and guarded validation.

The generated stress suite evaluated **2,300** mutations with a 300-case holdout: typo recall was
86.74%, false-correction rate 0%, unknown-term preservation 100%, and brand preservation 100%.
Warm latency was p50 8.57 ms, p95 16.33 ms, p99 20.34 ms, maximum 28.93 ms. Remaining misses are
retained by mutation class in `.local/query-understanding-stress.json`, without word-specific aliases.

Final validation: **243 tests passed**, four opt-in tests were deselected; Ruff passed for the
application, tests, and changed scripts; ESLint, TypeScript, and the Next.js production build passed.
Playwright reload acceptance retained the original `корондаш` chat text and result card, with no page
errors or horizontal overflow. No schema migration was needed.
## E2E retrieval parity, oracle holdout, and single active workspace (2026-10-03)

The production narrowing defect was downstream of Query Understanding. The correction produced
`normalized_query=карандаш` and concept `pencil`, but repository lexical predicates and the final
`ProductCategoryMatcher` still read `intent.raw_query=корондаш`. The pre-fix plan therefore combined
the resolved category with a mandatory typo-token text check. Retrieval now derives one
`retrieval_query = intent.normalized_query or intent.raw_query`; SQL FTS/literal/fuzzy predicates,
dynamic concept expansion, and final category validation all consume that same value. The original
query remains persisted for chat, history, and audit.

On the current PostgreSQL snapshot, the canonical `карандаш` query and corrected `корондаш` query
both produced 342 SQL candidates and 240 final product IDs. Their ID sets were exactly equal. Across
10 accepted canonical/typo pairs, exact pool parity was 100%, mean Jaccard was 1.0, false-zero rate
was 0%, and false-narrowing rate was 0%. The JSON evidence is
`.local/query-retrieval-parity.json`.

The separate seeded holdout benchmark generated and oracle-validated 3,739 cases from 179 real
taxonomy/catalog vocabulary terms, with 54 terms held out from 125 tuning terms. Aggregate
single-mutation recall was 79.14% (2,216/2,800), double-mutation recall 67% (134/200), and
triple-mutation recall 40% (80/200). Unknown preservation was 98.4% (492/500), brand/model
preservation was 91.67% (11/12), and 19/27 near-collision cases were conservatively preserved as
ambiguous. The benchmark also exposed 8 false corrections among unknowns, one among protected
brand/model inputs, and 8 among near collisions. Spell correction is therefore not claimed as fully
solved; the accepted-correction retrieval invariant is fixed, while precision and multi-error recall
remain measurable follow-up work. Per-mutation recall, precision, preservation, ambiguity and false
correction rates are stored in `.local/query-understanding-holdout.json`.

The workspace defect came from rendering independently loaded SIMPLE research and COMPOSITE hard
request state. The project now exposes one active query/revision, and the frontend renders exactly
one view from that state while retaining archived queries in History. Playwright verified
COMPOSITE → SIMPLE and SIMPLE → COMPOSITE, immediate removal of the old view, History retention,
and F5 restoration. Every state had either one Simple view or one Composite view, never both, with
zero page errors. Evidence: `.local/active-workspace-ui.json` and
`.local/final/ui/active-workspace.png`.

Final validation on this revision: 245 backend tests passed and four explicit opt-in tests were
deselected. Ruff passed for the application, tests, new migrations, and task diagnostics. ESLint,
TypeScript, and the Next.js 16.3.4 production build passed. PostgreSQL, FastAPI, Ollama, and the
production frontend were left running on ports 55432, 8000, 11434, and 3000.
## Query Understanding V2: precision-first decisions (2026-10-03)

V2 replaces the former nearest-candidate shortcut with an explicit `CorrectionDecision`: action
(`CORRECT`, `PRESERVE`, or `AMBIGUOUS`), confidence, raw score, runner-up score, margin, token and
candidate roles, evidence, rejection reason, and the five leading candidate traces. Candidate fusion
separates edit, Russian keyboard geometry, phonetic, morphology, layout/transliteration,
taxonomy/catalog quality, context, and cross-role penalties. Phonetic equality can no longer approve
a correction without independent mechanical or morphological evidence. Unknown input is explicitly
rejected when evidence or candidate margin is insufficient.

The vocabulary remains data-backed: canonical taxonomy, normalized ProductConcept rows, supplier
categories, and protected brand/model/article tokens sampled from SupplierOffer payloads. No typo map
was introduced. Known unsafe cases `тарцен`, `вумква`, `ценква`, `шопбер`, and `блогнот` are preserved
by the general evidence/role policy; desired pencil, pen, mug, umbrella, backpack, shirt, sock, and
raincoat errors still pass the deterministic path without Ollama, browser, or supplier network calls.

The frozen seed 20261003 used 179 vocabulary terms, split into 71 tuning and 108 unseen holdout terms,
and oracle-validated 3,778 cases. Compared with the supplied baseline, UNKNOWN false correction moved
from 4.0% to 0%; phonetic false correction from 8.4% to 2.0%; keyboard recall from 32.6% to 75.5%.
Macro precision was 96.63% and macro recall 68.09%. The unseen seed 20261004 independently generated
3,772 cases and measured UNKNOWN false correction 0%, phonetic false correction 2.5%, keyboard recall
75.0%, macro precision 94.62%, and macro recall 66.84%. Frozen warm latency was p50 48.56 ms and p95
74.61 ms before the weighted-keyboard prefilter; unseen latency after that optimization was p50 24.04
ms, p95 40.55 ms, and p99 57.06 ms.

The engineering goals were not all reached. Keyboard recall remains below 85%; phonetic false
correction remains above 1%; delete/insert/transpose and two/three-error recall remain below the
desirable targets; p95 remains above 30 ms. Precision improved substantially, but this is not reported
as complete spell recovery. Failure records, including score components and decisions, are written to
`.local/query-understanding-failures.json`; the two full reports are
`.local/query-understanding-holdout-20261003.json` and
`.local/query-understanding-holdout-20261004.json`.

Accepted high-confidence corrections retained exact live retrieval parity: exact pool parity 100%,
mean Jaccard 1.0, false-zero 0%, and false-narrowing 0%. Composite deterministic tests preserve target
case count, minimum quantity, position categories/colors, and `llm_used=false` for ordinary typo
recovery.

Final V2 validation: 250 backend tests passed and four opt-in tests were deselected; Ruff, ESLint,
and TypeScript passed. Live HTTP after FastAPI restart returned 240/240 pencil matches for
`корондаш`, stored the original and normalized forms separately, reported `CORRECT` with a 0.1636
candidate margin, and used neither Ollama nor background research. The live typo Composite request
also remained deterministic (`llm_used=false`) and preserved 50 cases, quantity 250, and all four
position-specific colors.

## Composite Assembly V2: maximum strict cases (2026-10-04)

The capacity losses had two independent causes. Composite eligibility used the general procurement tier evaluator, which admitted confirmed incoming, remote, preorder, factory, and order-on-demand supply into Case View. The assembler then used a sequential greedy choice, so an early position could consume the only canonical product usable by a later position even when a larger valid matching existed. Retrieval itself had already removed the earlier pre-filter `LIMIT`, but hydrating every wrong-color offer made the full-pool path unnecessarily expensive.

The current pipeline retrieves the complete category pool without a row limit, records its count, then applies the structured offer-color predicate in PostgreSQL before hydration. Python validates the category, exact offer color, observation freshness, `ON_HAND` state and numeric `free_quantity >= requested`; it never combines color from one offer with stock from another. Incoming, remote, preorder, factory and order-on-demand evidence is retained as diagnostic alternative supply but cannot enter Case View. Missing, stale and unknown observations are classified as recoverable; explicit insufficient free stock is rejected and does not consume refresh budget.

Capacity is now computed with deterministic maximum bipartite matching between all case-position slots and canonical product identities. `target_cases` is an upper bound. The same canonical product cannot occur twice within a case or across cases. The production matcher matched exhaustive brute force on all 250 seeded overlapping-pool fixtures. Alternatives are allocated only after every primary selection, from identities unused anywhere else. Existing product semantics are preserved: a `COMPLETE` case requires one selected strict-eligible offer in every requested position and stores up to three alternatives per position. A shortage of alternatives does not invent products or erase an otherwise complete case.

The live `BLUE umbrella / pencil / pen / mug >=20` snapshot initially produced strict eligible pools of 27, 3, 1, and 8 canonical products. Therefore both theoretical and assembled capacity were 1 of 50, with pen as the proven bottleneck. Raw position pools were 901, 5,253, 908, and 342 offers; structured blue matches were 196, 483, 139, and 35. The initial result contained zero duplicate products and zero duplicate case signatures. Bounded background recovery later raised the active capacity from 1 to 7 and created a new persisted revision; Playwright saw the same seven cases before and after F5, with no page errors or horizontal overflow.

The live `BLUE umbrella / BLACK raincoat / WHITE socks / RED tshirt >=250` snapshot produced strict eligible pools of 23, 0, 0, and 0. Capacity was correctly 0 of 50. The funnel proves that the result is an availability coverage gap rather than a missing catalog: raincoat had six category matches and one color match but no availability evidence; socks had 3,513 category matches and 345 color matches but no fresh observation; tshirt had 91 category matches, 15 color matches and one fresh current offer, but none with sufficient free stock after strict validation. Umbrellas were not the bottleneck.

Availability coverage at the audited snapshot was:

| Supplier | Offers | With availability | Fresh | Stale | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| ArteGifts | 3,989 | 3,848 | 86 | 3,762 | 96.47% |
| Gifts | 7,512 | 3,831 | 50 | 3,781 | 51.00% |
| HappyGifts | 2,586 | 863 | 96 | 767 | 33.37% |
| Oasis | 80 | 43 | 0 | 43 | 53.75% |
| Portobello | 708 | 583 | 0 | 583 | 82.34% |
| Ucontay | 23,966 | 7,393 | 1,252 | 6,141 | 30.85% |

These figures explain the current capacity ceiling and are not presented as complete supplier coverage. Stable products remain in the index when observations expire; only strict quantity eligibility changes. Supplier-specific raw evidence and parser versions remain available in the coverage artifact.

The uncontended first four full-pool SQL benchmark runs were 249.86, 251.06, 253.86, and 355.82 ms. During the same benchmark, live background recovery began writing refreshed observations and the remaining runs rose to 4.70-5.40 seconds; the all-run p50/p95 were 5,019.78/5,395.24 ms. This contention is retained in the artifact rather than discarded. The synchronous Composite request performed zero browser invocations; Chromium appeared only in queued background recovery and in the explicit Playwright acceptance.

`DIVERSE` now represents deterministic `ANY_BASE_COLOR`: it accepts known normalized colors, rejects `UNKNOWN`, and uses stable category/product hashing rather than `random()` or request IDs. Position-specific and global required colors continue to use the shared color normalizer and blue shade family. The full funnel includes before/after/removed counts and machine-readable reasons for color mismatch/unknown, missing or stale evidence, unknown quantity, insufficient/reserved stock, alternative-only supply, variant rejection and canonical duplication.

Evidence is stored in `.local/composite-assembly-live.json`, `.local/composite-funnel.json`, `.local/composite-availability-coverage.json`, `.local/composite-allocator-validation.json`, `.local/composite-performance.json`, `.local/composite-recovery-live.json`, and `.local/composite-ui-e2e.json`. Final validation: **254 backend tests passed**, four explicit opt-in tests were deselected; Ruff, ESLint, TypeScript, Next.js 16.3.4 production build, and Playwright F5 acceptance passed. Remaining limitations are live catalog freshness and supplier coverage, especially Oasis and Portobello at this snapshot; they are not bypassed by weaker availability semantics.

## Conversational Query Router (2026-10-04)

The zero-result follow-up defect was caused by routing order. The universal project endpoint parsed every message as a fresh product request before it consulted the active workspace. Because the search planner always supplies a category fallback, `оставь синие` created a standalone SIMPLE query and searched that phrase instead of refining pencils. The older refinement engine also contained damaged Russian matcher literals, so it could not reliably apply the correct operation even when reached.

The endpoint now loads the server-side active `ProjectQuery` first and runs an explicit deterministic router before SIMPLE/COMPOSITE parsing. Its action contract is `NEW_SEARCH`, `REFINE_CURRENT`, `MODIFY_CURRENT`, `EXPAND_CURRENT`, `REMOVE_CONSTRAINT`, `RESET_FILTERS`, and `COMMAND`. Attribute-only language and anaphora preserve the active subject; a new subject becomes a new search only with strong lexical evidence. Each route records confidence, original and normalized message, current and detected subjects, signals, reason, latency, and whether an LLM was used. The tested paths use no LLM.

For SIMPLE refinement the active `ResearchSession` remains the source of truth. Filtering operates on all persisted `ResearchSession.products`, not the first rendered page. The previous view is stored in history, repository save increments the durable revision, and undo restores that view with a new version. The original query remains the workspace title. For Composite, a global color follow-up creates a child `HardRequestRecord` revision and binds the normalized color family to every position; the original request remains in history.

On the live PostgreSQL snapshot, `карандаш` produced a pool and matching count of 240. `оставь синие` returned `REFINEMENT / REFINE_CURRENT`, retained the same research ID and pool 240, and produced 17 blue matches. No second `ProjectQuery` was created. `отмени` restored 240 matches. `найди кружки` returned `NEW_SEARCH`, created a second query, and used a different research ID. A two-position Composite followed by `оставь синие` created a child revision and applied the six accepted blue-family values to both positions.

The frozen seed 20261004 generated and oracle-validated 2,400 routing cases. All 2,400 passed; high-confidence subject loss was 0%, false inheritance was 0%, and deterministic latency was p50 0.0069 ms, p95 0.0088 ms, max 0.0622 ms. Playwright confirmed the pencil title and 17 filtered cards, then confirmed the same active filter and conversation after F5 and restored 240 results through `отмени`, with no page errors.

Evidence is stored in `.local/conversation-router-holdout.json`, `.local/conversation-router-live.json`, and `.local/conversation-router-ui.json`. No schema migration was required. Final validation: **266 backend tests passed**, four explicit opt-in tests were deselected; Ruff, ESLint, TypeScript, Next.js 16.3.4 production build, and the live Playwright acceptance passed. The deterministic operation executor covers colors, materials, price, reset, undo, selection, similarity, refresh, and continuation. Cross-category SIMPLE expansion and arbitrary brand/availability removal still rely on the existing semantic refinement fallback and are not claimed as fully covered by the generated high-confidence suite.

## Composite without quantity and optional-constraint null safety (2026-10-04)

The production 500 for `собери 50 наборов из футболок, ручек, бутылок и кружек` came from treating the existence of a `QuantityConstraint` object as proof that a numeric minimum was active. The normalized intent correctly contained `target_sets=50`, `quantity.minimum=null`, and `quantity.required=false`, but two search diagnostics evaluated `free_quantity >= None`. The main eligibility branch already accepted a catalog product when no quantity was requested; the crash was in `free_gte_required` and `fresh_on_hand_free_gte_required`.

`QuantityConstraint.required_quantity` is now the canonical active value, with matching accessors on the effective position and complete request. Validation derives `required` from whether `minimum` exists. Retrieval, strict eligibility, tier ordering, recovery scheduling, diagnostics, case persistence, and helper predicates consume that representation. Without quantity, availability remains informational and does not filter stable catalog products. Quantity metrics report `quantity_metric_applicability=NOT_APPLICABLE`, `required_quantity=null`, and both `>= required` counters as null. Availability tiers still describe actual fresh evidence; unknown or stale stock is not mislabeled as current merely because the product is catalog-eligible. Explicit `от 250 шт` continues to require fresh numeric `ON_HAND.free_quantity >= 250`.

The failure boundary now catches execution errors after record creation, stores `FAILED` with bounded `failure_code`, `failure_stage`, and `failed_at`, and marks the associated query failed. A new query does not replace `Project.active_query_id` until initial assembly succeeds. If later background assembly fails, the prior active query is restored. Startup recovery uses the same guarded boundary, so an exception during resumed assembly cannot leave a permanent `RUNNING` record. An injected post-creation `RuntimeError` verified the terminal state and preservation of the prior workspace.

The repository audit reviewed 214 candidate optional-value references across quantity, price, color, material, brand, capacity, filters, diagnostics, serialization, SSE, recovery, and result counters. Nine Composite quantity usages were changed: two unsafe numeric comparisons and seven downstream reads were moved to the canonical active value. Price comparisons already checked both the constraint and product price, and color/material collections already used explicit empty/required semantics. A generated matrix exercised 2,592 valid combinations of absent/present quantity, price, color, materials, availability evidence, target case count, and one to six categories through normalized constraints, eligibility, assembly, and serialization with zero unexpected exceptions.

On the current PostgreSQL snapshot the exact request returned HTTP 202, used no Qwen or synchronous browser, retained `target_cases=50`, `minimum_quantity=null`, and assembled 50 complete globally unique cases. The no-quantity funnel was: pens 5,270 raw / 3,261 category / 3,261 eligible before dedup / 3,143 unique; mugs 925 / 924 / 924 / 900; bottles 2,611 / 2,241 / 2,241 / 2,124; T-shirts 3,875 / 3,853 / 3,853 / 3,836. Availability-known/fresh counts were informational: pens 1,282/11, mugs 774/0, bottles 1,374/0, and T-shirts 1,948/0. The 600 persisted candidate placements contained 11 tier-A and 589 tier-D placements, preserving uncertainty rather than fabricating current stock. The smallest full pool was mugs at 900, so the requested capacity of 50 was reached without using stock as an implicit constraint. The measured HTTP latency was 55,447.20 ms while background supplier workers were contending for PostgreSQL; synchronous `browser_invoked` remained false.

The explicit-quantity contrast returned HTTP 202 with `minimum_quantity=250` and zero cases because T-shirts, bottles, and mugs had zero strict eligible offers at that snapshot. The typo request without quantity also produced 50 cases deterministically, and `теперь от 250 штук` preserved all four positions and the 50-case target while activating quantity 250. Playwright rendered 10 cases on the first page before and after F5, persisted the conversation, kept one active workspace, and reported no page errors or horizontal overflow. A FastAPI restart restored the same active revision. Evidence is in `.local/composite-no-quantity-live.json`, `.local/composite-no-quantity-ui.json`, `.local/composite-no-quantity-restart.json`, and `.local/final/ui/composite-no-quantity.png`.

Final validation on this change: **270 backend tests passed**, four explicit opt-in tests were deselected; Ruff, ESLint, TypeScript, and the Next.js 16.3.4 production build passed.

## Typed Composite intent mutations and adversarial state audit (2026-10-05)

Composite follow-ups now produce a typed `IntentPatch` list before changing the active intent. Each patch records operation, scope, field, target position, replacement/value, source text, confidence and resolver. Supported deterministic operations cover global and position-scoped color, material, price and quantity changes; target-case changes; add, remove, replace and keep-only position changes; semantic preferences; and undo. The executor applies every patch to a copy of the previous plan, validates the resulting `HardRequestIntent`, and publishes a new immutable `HardRequestRecord` revision. Previous revisions remain readable in History.

Position lookup uses the shared taxonomy resolver plus bounded morphological category patterns for conversational case forms. Multi-clause messages are split before color extraction, so `футболки красные, ручки синие, бутылки белые` creates three independent position patches and does not leak a combined color set into mugs or global state. Query Understanding V2 still runs first in the HTTP path; the mutation layer adds no LLM or browser call.

Procurement quantity and requested case count remain separate fields. `сделай 20 наборов` changes only `target_sets`; `теперь от 20 штук` activates strict procurement; `количество не важно` returns the required quantity to `None` without changing the case target. Price has independently optional minimum and maximum values. Material inclusion/exclusion and price filters are evaluated only when active.

Mutations are serialized with a per-project application lock. Concurrent follow-ups therefore read the active revision after the preceding mutation commits instead of branching from the same stale revision. Background recovery verifies that its subscribed request is still the project's active query before publishing, so an old refresh cannot replace a newer intent.

The deterministic adversarial test generated 5,000 reproducible mutation sequences of 3–15 steps and passed every produced plan through patch planning, validation and JSON serialization with zero unexpected exceptions. The required chain covered local red T-shirts, local blue pens, adding a black backpack, replacing bottles with white thermomugs, removing umbrellas, setting and clearing procurement quantity, changing the target case count and keeping only selected positions. A separate multi-position test verifies no cross-position color leakage.

Live PostgreSQL/API acceptance created eight immutable revisions for `зонты / ручки / бутылки / футболки`: local red T-shirts, local blue pens, quantity 20, bottle-to-mug replacement, black-backpack addition, target reduction to 20, and quantity removal. Every response was HTTP 202, every deterministic step reported `llm_used=false`, and the final plan retained five positions, target 20 and `minimum_quantity=null`. The strict-quantity intermediate revisions retained `minimum_quantity=20`. Under active background database contention the individual synchronous latencies ranged from 4.0 to 29.0 seconds; this run is correctness evidence, not a latency target. Raw evidence is `.local/composite-mutation-live.json`.

Playwright restored the mutated Composite after F5 with one `[data-testid=hard-request]` workspace, no Simple grid, the persisted follow-up message, no page errors and no horizontal overflow. Evidence is `.local/composite-mutation-ui.json` and `.local/final/ui/composite-mutation.png`.

Validation on this revision: **274 backend tests passed**, four explicit opt-in tests were deselected. Ruff, ESLint, TypeScript through the Next.js 16.3.4 production build, and the production build passed. PostgreSQL, FastAPI, Ollama and the production frontend remain running on ports 55432, 8000, 11434 and 3000.

## Composite mutation E2E gap audit (2026-10-06)

The canonical ten-message mutation chain was executed through the live HTTP API and PostgreSQL. Every response was HTTP 202 and every immutable revision was inspected. The final effective intent contained exactly `pen/BLUE`, `tshirt/RED`, and `backpack/BLACK`, with `target_cases=20` and no required quantity. The full typo chain from the task produced the same effective final state. It caused no false `NEW_SEARCH` route and no HTTP 500. Detailed per-step routing, Query Understanding evidence, revisions, latency, and normalized intent are stored in `.local/composite-typo-chain.json`.

This acceptance found three integration defects. Quantity-clear wording could be routed as a new search after Query Understanding changed a noun in the source phrase; command routing now evaluates both original and normalized text. General typo variants of add/remove/keep-only commands were not consistently recognized; bounded command grammar now accepts those morphological variants without adding the supplied sentences to a typo map. Current Composite subjects were compared as raw Russian labels against canonical detected concepts, which made keep-only create a new workspace; the endpoint now resolves persisted position labels through the shared category resolver before routing.

Four true concurrent HTTP scenarios were executed against PostgreSQL: independent shirt-color and pen-color changes, conflicting colors on the same shirt position, concurrent backpack addition and bottle removal, and concurrent quantity and shirt-color changes. Both independent mutations survived, add/remove and quantity/color produced the combined expected state, both conflicting messages and revisions were persisted, and exactly one revision remained active. The observed conflict policy is serialization by lock acquisition order, so the last committed patch wins. Evidence is in `.local/composite-concurrency.json`.

The read-modify-write boundary is protected by a PostgreSQL advisory lock keyed by project ID. A dedicated connection holds the lock across loading the active revision, understanding and planning the patch, building and persisting the immutable revision, and activating it. The existing process-local lock remains an optimization; correctness no longer depends on one FastAPI process. Different project IDs use independent lock keys. Lock acquisition time is included as `db_lock_wait_ms` in operation diagnostics.

Repeated Undo exposed a separate history defect: the second Undo selected the prior Undo revision instead of walking farther back through committed semantic revisions. Undo now records the restored revision, skips Undo records, and restores the preceding non-Undo state. Its PostgreSQL regression verifies revision 3 → revision 2 → revision 1.

Validation on this revision: **278 backend tests passed**, with four explicit opt-in tests deselected. Ruff, ESLint, TypeScript through the Next.js production build, and the production build passed. The earlier reproducible 5,000-sequence domain state suite remains present. Live canonical/typo acceptance covered 20 mutation messages with zero unexpected HTTP 500; concurrency covered eight simultaneous mutation requests across four projects.

The complete Definition of Done is **not claimed**. The requested additional 200–500 generated full PostgreSQL/HTTP sequences and the separate 500-message typo mutation holdout were not executed; consequently operation-count coverage, aggregate retrieval/assembly mismatch counts, typo false-correction rates, and wrong-position rates at those requested sample sizes are unavailable. The stale recovery/SSE race, old Composite recovery after a new SIMPLE search, full-chain Playwright F5, and full-chain API-restart acceptance were also not newly executed for this exact ten-step chain. Existing narrower regressions do not replace those required measurements.

## FINAL COMPOSITE MUTATION VALIDATION (2026-10-08)

### PASSED

The generated state-machine ran **300 deterministic sequences** (seed `20261006`) through eight real FastAPI processes and PostgreSQL. The sequences contained **1,019 HTTP mutations**; all 1,019 returned HTTP 202, expected 4xx was 0, unexpected HTTP 500 was 0, persisted-state mismatches were 0, recorded retrieval mismatches were 0, and assembly violations were 0. An independent oracle implemented the expected state without calling production `IntentPatch.apply`. Every step retained expected and actual semantic diffs in the artifact. Deterministic cross-position leakage, subject loss, constraint loss, wrong global/local scope, and target-case/quantity confusion were all 0 in this corpus. `.local/final/composite-generated-failures.json` is an empty array.

Operation counts were: `NEW_COMPOSITE=320`, `NEW_SIMPLE=20`, `SET_POSITION_COLOR=72`, `CLEAR_POSITION_COLOR=32`, `SET_GLOBAL_COLOR=33`, `CLEAR_GLOBAL_COLOR=33`, `SET_MATERIAL=68`, `EXCLUDE_MATERIAL=34`, `CLEAR_MATERIAL=34`, `SET_PRICE=67`, `CLEAR_PRICE=33`, `SET_QUANTITY=65`, `CLEAR_QUANTITY=32`, `SET_TARGET_CASES=32`, `ADD_POSITION=31`, `REMOVE_POSITION=31`, `REPLACE_POSITION=31`, `KEEP_ONLY=31`, and `UNDO=20`. The SIMPLE cases were followed by new Composite requests, covering both workspace directions. Full evidence is `.local/final/composite-generated-db-http.json`.

The fresh typo holdout used seed `20261007` and **512 generated follow-up messages** across delete, insert, transpose, keyboard-neighbor, phonetic, inflection, word-order and two-error mutations. Router action accuracy was **93.36%**, patch accuracy **68.16%**, false `NEW_SEARCH` was 0, wrong-position mutation was **0**, preserve/ambiguous was **31.84%**, and LLM fallback was 0. The lower patch recall was retained rather than weakening the precision-first correction threshold. Artifact: `.local/final/composite-typo-holdout.json`.

The delayed R1 recovery test kept R3 active after red-shirt and blue-pen mutations: stale overwrite count 0. Completing an older Composite recovery after a pencil SIMPLE search left the SIMPLE workspace active: old-search reactivation count 0. Failure injected after the PostgreSQL advisory lock produced `FAILED`, preserved the previous active revision, released the lock, and allowed the next mutation to complete. Artifact: `.local/final/composite-sse-races.json`.

All four concurrency scenarios were repeated successfully. A separate multi-process run sent simultaneous non-conflicting mutations for one project through API processes on ports 8000 and 8001; both HTTP responses were 202, both changes survived, and exactly one revision was active. PostgreSQL transaction-scoped advisory locking remains the final concurrency guard. Artifacts: `.local/composite-concurrency.json` and `.local/final/composite-multiprocess.json`.

The isolated PostgreSQL Playwright acceptance completed all ten browser submissions and inspected the workspace after every step. Its final revision contained pen/BLUE, T-shirt/RED and backpack/BLACK, `target_cases=20`, and `required_quantity=null`. F5 restored the same revision and state. The first Undo restored the prior mug/WHITE state without quantity; the second restored the preceding mug/WHITE state with quantity 250. This run exposed a real defect: assembly diagnostics replaced the revision metrics and erased `operation.restored_revision`, causing repeated Undo to jump forward. `_prepare_and_build` now merges assembly metrics with lifecycle/operation metadata; the browser rerun and backend regression pass. Active-workspace duplication was 0, page errors were 0, and horizontal overflow was false. Artifact: `.local/final/composite-playwright-final.json`.

Final automated validation after the fix: **282 backend tests passed**, four explicit opt-in tests were deselected. Ruff, ESLint, standalone TypeScript `--noEmit`, and the Next.js 16.3.4 production build passed.

### FAILED

No deterministic generated sequence, concurrency scenario, race scenario, or accepted high-confidence typo mutation violated a required safety invariant. No minimized failure reproducer was produced because the final 300-sequence rerun had zero failures.

### NOT TESTED

The 300-sequence run used a dedicated migrated PostgreSQL database on the same local server so test projects could be isolated without polluting normal user projects. It exercised the real HTTP, Query Understanding, router, patch, persistence, retrieval, availability, assembly and serialization code paths, but its catalog was empty. Consequently the generated suite proves null/zero-capacity behavior and intent/persistence invariants; it does **not** provide 1,019 independent positive-product checks against the populated catalog. Positive pool and procurement behavior remains covered by the earlier live canonical chain and focused availability/assembly tests.

The requested total was described as approximately 1,500–3,000 mutations; the required 300 sequences of 3–10 messages produced 1,019 messages because this generator was weighted toward short sequences. No additional run was presented as if it met the approximate message range.

The typo artifact did not separately calculate `false_REFINE`, false-correction, or accepted-position accuracy beyond wrong-position mutation; those metrics remain **NOT TESTED** at the requested 512-message granularity.

The final UI `карандаш` transition was checked in the isolated run by activating SIMPLE and observing one SIMPLE workspace and zero Composite workspaces. A later attempt to repeat the entire chain on the populated main database completed the first persisted Composite revision but stalled before the browser advanced to step 2 and was stopped. Therefore the populated-main-database ten-step Playwright rerun and direct browser `карандаш` submission are **NOT TESTED** in the final artifact.

Because these required measurements remain incomplete, **FULL COMPOSITE MUTATION DoD is not claimed**.

## Composite performance optimization and cleanup (2026-10-08)

### Performance

The populated `souvenir` index was profiled with the exact query `собери 50 наборов из футболок, ручек, бутылок и кружек` and its strict `от 250 шт` variant. The baseline processed the complete raw position pools of 5,309 / 943 / 2,625 / 3,880 offers. The quantity-less result assembled 50 cases; the strict result assembled zero because the current fresh procurement evidence did not satisfy every position. The optimized run retained those pool sizes and capacities.

| Metric | Before | After |
|---|---:|---:|
| Quantity-less persisted execution | 27,344 ms | 5,545 ms |
| Quantity-less candidate pipeline | 24,983 ms | 4,680 ms |
| Quantity-less SQL retrieval/hydration wall | 2,946 ms | 4,508 ms under concurrent background refresh |
| Quantity-less filtering/diagnostics/assembly | 22,037 ms | 173 ms |
| Strict persisted execution | 27,045 ms | 2,429 ms |
| Strict candidate pipeline | 26,631 ms | 2,326 ms |
| Strict filtering/diagnostics/assembly | 21,459 ms | 71 ms |
| Matching raw pools | 5,309 / 943 / 2,625 / 3,880 | unchanged |
| Assembled cases, no quantity / strict 250 | 50 / 0 | 50 / 0 |

The slow path was not PostgreSQL matching or maximum case allocation. Fine-grained instrumentation showed category validation at 213–578 ms, canonical identity extraction at 6–8 ms, and assembly at 0.04 ms for zero capacity or 93 ms for 50 cases. Eligibility diagnostics consumed 21–22 seconds because `product not in color_matched` performed deep Pydantic equality in a list membership test for every full-pool product, producing O(N²) behavior. It now uses the same offer identity as a set membership test; eligibility/diagnostics fell to 54–87 ms without changing a pass/reject decision.

Hydration now carries `SupplierOffer.product_id` into the transient Product metadata, avoiding a second large `IN (...)` identity query. Legacy/test DTOs retain a bounded compatibility lookup. Query/catalog evidence tokenization is cached, and expensive name taxonomy resolution is deferred until its fallback is actually needed. The assembler uses deterministic Hopcroft–Karp matching instead of recursively rescanning every full position pool. Existing exhaustive allocator regressions still compare production output with brute force.

`EXPLAIN (ANALYZE, BUFFERS)` for the structured-color retrieval showed bitmap scans on category, Russian FTS and normalized-color GIN indexes; product/observation joins used index scans, index-only scans and hash/nested-loop joins. Measured executions were 5.52 ms for pencils, 36.43 ms for umbrellas, 39.14 ms for mugs and 66.20 ms for pens. No temporary blocks or disk sort spill occurred. No new index was added because the plans did not justify one.

Peak process memory was not captured before the code change, so no before/after memory claim is made. The optimization reduces repeated model comparisons and removes a duplicate identity result set, but that is not reported as a measured memory reduction.

Availability semantics are unchanged: `required_quantity=None` keeps catalog membership independent of stale/unknown availability; strict quantity accepts only fresh numeric `ON_HAND.free_quantity`. Total, reserved, incoming, remote, unknown and stale evidence were not merged or promoted. No hidden `LIMIT`, slice, top-N or early break was introduced.

### Functional verification

The exact quantity-less and strict requests returned HTTP 202 with `llm_used=false` and no synchronous browser invocation. Position-specific and quantity set/clear paths were exercised in the live chain. On the populated database the ten Playwright mutations completed and the final state was reached; F5 and the first Undo completed. The runner then waited indefinitely for the second browser Undo transition and was stopped. Repeated Undo remains covered by the permanent PostgreSQL regression, but the second Undo and subsequent SIMPLE transition in this final populated-database browser run are **NOT TESTED** and are not reported as passed.

Final automated checks after optimization and cleanup: **282 backend tests passed**, four explicit opt-in tests were deselected. Ruff passed for `apps/api/app` and `apps/api/tests`; ESLint, standalone TypeScript `--noEmit`, and the Next.js 16.3.4 production build passed. Repository-wide Ruff over all historical diagnostic scripts still reports pre-existing style findings, so only the established application/test Ruff scope is claimed. `git diff --check` found no whitespace errors; Git emitted only existing LF-to-CRLF conversion warnings.

### Cleanup

Inventory classified `hard_requests.py` and `intent_mutation.py` as PRODUCT CODE, and `test_hard_requests.py`, `test_intent_mutations.py` and `test_composite_assembly_v2.py` as PERMANENT REGRESSION TESTS; all were preserved. The generated HTTP, typo-holdout, Playwright, SSE-race and multi-process runners were TEMPORARY ACCEPTANCE SCRIPTS with no code, CI or documentation references and were deleted. `probe_parse.py` was already absent. Older scripts whose ownership or production diagnostic role was not proved were classified UNKNOWN and preserved.

Cleanup removed **5 scripts** and **86 generated JSON/log/screenshot/profile files**, totaling **10,065,888 bytes**. No `composite-*` or `api-800*` disposable artifact remains in `.local`, `.local/final` or `.local/final/ui`. No listener remains on ports 8001–8016.

Database identity was checked before cleanup: the protected main database is `souvenir` on port 55432. The disposable `souvenir_composite_e2e` database was proven to be the isolated generated-acceptance database, had zero active sessions, differed from every protected database, and was dropped. Other benchmark, migration and test databases were preserved because this cleanup did not prove their ownership sufficiently. Four acceptance users and their nine projects were deleted only after their exact recorded IDs and complete ownership sets matched the run manifests; no name-based broad deletion was used. The fourth user was the post-cleanup smoke identity.

### Database safety

- Main database dropped: **no**.
- Main schema truncated: **no**.
- Catalog rows deleted: **no**. Current snapshot remains 39,161 indexed products, 40,025 offers, 40,025 product observations and 18,084 availability observations.
- Real user/project/history data deleted: **no**; only nine ID-manifested acceptance projects and their four isolated owners were removed.
- Catalog jobs/checkpoints reset or deleted: **no**; 273 persisted jobs remain.
- Background scheduler/workers disabled: **no**.

Post-cleanup health checks passed for PostgreSQL, FastAPI, Ollama and the production frontend on ports 55432, 8000, 11434 and 3000. A disposable post-cleanup identity also received HTTP 202 for SIMPLE, COMPOSITE, a position mutation and Undo; its exact three-project ownership set was then deleted with the user. The remaining limitations are the unmeasured baseline peak memory and the incomplete second Undo/new-search portion of the final populated-database Playwright run; full UI DoD is therefore not claimed for this run.
