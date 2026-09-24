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
