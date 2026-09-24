# Research: реализация и проверка 16 сентября 2026

Код Research реализован поверх существующего проекта. Полный Definition of Done **пока не подтверждён**: live-обходы ограничены бюджетом, Oasis возвращал HTTP-ошибку, у ArteGifts встретилась ошибка разбора. Ни число результатов, ни наличие шести адаптеров не означают полного покрытия каталогов.

## 1. Search → Research

Сохранены SearchService, Orchestrator, SupplierProvider, BrowserEngine, CurrencyService, ColorNormalizer, Chat/Message/AgentExecution, старые API и компоненты workspace. ResearchService добавляет длительный обход и устойчивые checkpoints. Site-specific extraction остаётся в providers. Старый план Stage 3 Product Selection Workspace заменён Research Workspace.

## 2–3. Шесть поставщиков и исследование сайтов

Все шесть зарегистрированы в DI, включая вновь активный Oasis. Контракт расширен optional-методами research_listing и research_products; старые search/get_product сохранены.

| Источник | Проверенные данные и особенности |
|---|---|
| Oasis | Существующая форма и data-catalog-product, страницы page-N, варианты как URL/color. Live acceptance получил HTTP-ошибку; работоспособность полного обхода не подтверждена. |
| Ucontay | Поиск collection/all, product-preview, pagination-next, варианты существующего parser. Не применяются старые ограничения числа товаров. |
| Gifts | Реальный поиск, ArticlesData, ссылки следующей страницы; характеристики карточки и описание. RUB конвертируется существующим CurrencyService. |
| Portobello | Angular ng-state, Catalog_Products_Paginator и наблюдаемый GraphQL StocksDataLoader. Только свободный остаток склада MAIN; COMING/REMOTE/SAMPLES исключены. inStock не означает доступность сейчас. |
| HappyGifts | Каталог бутылок, product-card, next-pag, активный цвет и центральный свободный остаток. Скрытая CAPTCHA формы входа не считается блокировкой каталога. |
| ArteGifts | Каталог бутылок, load-more, variant configurator JSON с publicPriceRrc KZT и available. Неподходящий JSON-LD рекомендаций не используется. |

Полные наблюдения: [Oasis](providers/oasis.md), [Ucontay](providers/ucontay.md), [Gifts](providers/gifts.md), [Portobello](providers/portobello.md), [HappyGifts](providers/happygifts.md), [ArteGifts](providers/artegifts.md). HTML, сетевые ответы и скриншоты сохранены локально в `.local/research/six`; селекторы взяты из этих наблюдений.

## 4–7. Intent, Planner, Expansion, Discovery

ResearchIntent расширяет SearchIntent: категории и подтверждённый stock ≥ quantity проверяются детерминированно. Цвет по умолчанию — семейство; «строго» включает точный режим. BLUE включает navy, dark, royal, light и blue-grey. Предпочтения меняют ранжирование, не создают факты.

ResearchPlanner строит category → supplier → query/route. QueryExpansion использует ограниченную таксономию: бутылка, бутылка для воды, спортивная бутылка, термобутылка; кружки автоматически не добавляются. CategoryDiscovery для IT-конференции формирует 10 категорий с primary/secondary/exploratory приоритетами. Начальная discovery сейчас словарная; произвольная AI-генерация новых категорий не реализована.

## 8–11. Coverage, Jobs, Background, Streaming

ResearchCoverage хранит страницы, просмотренные/проверенные/отброшенные наблюдения, reported total и его единицу, query/route, время, ошибки и курсор с pending/failed/visited URLs. Счётчики наблюдений могут включать повторные предложения из разных запросов; размер уникального пула выводится отдельно.

ResearchJob имеет QUEUED/RUNNING/PARTIAL/COMPLETED/FAILED/CANCELLED. Задание asyncio живёт независимо от HTTP и подключения SSE. Перезапуск переводит прерванные задания в PARTIAL; продолжение запускается явно. Cursor и pool сохраняются одной транзакцией. Есть отмена, общий semaphore запросов, задержки и retry/backoff. Не запускать несколько API workers против одной базы.

Каждая обработанная карточка обновляет durable pool. SSE публикует текущий revision примерно раз в секунду, одна activity-панель показывает прогресс по источникам. Это полные snapshots, не delta-протокол; для больших пулов потребуется оптимизация.

## 12–13. Lifecycle и свежесть

ResearchSession хранится в отдельной таблице, привязан к существующему chat/project. Legacy TTL cleanup не удаляет research. Product.fetched_at определяет FRESH/STALE после 3600 секунд. История, selection и результаты сохраняются. Explicit refresh обновляет наблюдения; ранее принятые, но теперь неподходящие предложения исключаются из валидного представления.

## 14–17. Refinement, Similarity, Facets, Groups

ResearchRefinementEngine различает LOCAL_FILTER, LOCAL_RERANK, LOCAL_SEMANTIC_REFINE, SIMILARITY_SEARCH, TARGETED_RESEARCH, EXPAND_RESEARCH, REFRESH_RESEARCH, RESET_REFINEMENT, UNDO, SELECT, SHOW. Очевидные команды детерминированы; остальные используют существующий structured LocalLLMProvider и компактный контекст: последние шесть сообщений и максимум 24 summaries.

ResearchDecision валидируется Pydantic. Поля не относящегося к действию назначения удаляются. Например, rerank не может незаметно задать фильтры или выбрать IDs. Неизвестные IDs отвергаются. Модель не создаёт товары/цены/остатки и не получает произвольные browser/shell/SQL tools. Qwen3:4b реально проверен на «выглядят дороже своей цены»: LOCAL_RERANK, premium, без сторонних изменений.

SimilarityService сравнивает категорию, цвет, цену и текстовые факты. При недостатке соседей возможен targeted research по наблюдаемому материалу/бренду. Image embeddings отсутствуют. FacetService строит доступные категории, цвета, материалы, объём, бренд и raw attributes. ProductGroupingService объединяет вероятные модели, сохраняя offer IDs; UI позволяет раскрыть варианты или показать все. Это эвристические кандидаты, не доказанная идентичность SKU разных поставщиков.

## 18–22. COMPLETE, LIMITED, targeted, discovery, continuation

COMPLETE требует естественного завершения pagination, отсутствия pending/failed URLs и ошибок разбора, а также проверки reported total в worker. Источник считается завершённым только когда завершены все его запланированные ветки. LIMITED означает бюджет времени/веток либо несовпадение total. CAPTCHA, TIMEOUT и FAILED не скрываются за COMPLETE.

Targeted research добавляет только нужные category/query branches. Уже COMPLETE не повторяются; незавершённая targeted-ветка возобновляется. При неизвестных материалах система может добрать факты вместо ложного локального вывода. Ambiguous discovery распределяет время между категориями; slice по умолчанию 45 секунд сохраняет незавершённый cursor. «Продолжить исследование» работает по незавершённым веткам и не обрезает пул top-N.

## 23–25. Database, API, frontend

Alembic `0003_research` добавляет research_sessions и research_jobs, не дублируя Chat/Project. Payload содержит typed aggregate, coverage, view и до 30 снимков undo. Миграция применена к локальной PostgreSQL.

API: POST `/api/researches`; GET `/api/researches/chat/{chat_id}` и `/{id}`; PATCH `/{id}/view`; POST `/{id}/messages`, `/continue`, `/cancel`; GET `/{id}/events` и `/products/{product_id}`. Создание возвращает 202, конфликт версии 409, неизвестный product ID 422, превышение числа jobs 429. Research не возвращает TTL 410: данные остаются stale.

Frontend: `components/research/{research-workspace,research-filters,research-progress}`, `hooks/use-research`, `lib/research`, `app/research.css`. Переиспользованы ProjectList/Form, ProductCard, ProductDetails, Sheet, API helper и design tokens. Desktop — проекты/результаты/chat; mobile — drawer и нижняя навигация. Сервер определяет filters/selection/history; reload восстанавливает их. Карточки показывают KZT, фото, атрибуты и match; supplier/stock/SKU доступны только в details. Весь пул доступен через «Показать ещё», по 50 карточек. Реализованы focus/Escape, reduced motion и image fallback.

## 26. Проверки

Новые `test_research.py` и `test_research_api.py` проверяют intent/discovery/expansion, запрет ложного COMPLETE, 60 результатов через две страницы без top-N, freshness/persistence, локальный refinement без browser, similarity/grouping/facets, typed IDs, CAPTCHA/TIMEOUT, cancel/resume, targeted branches и action scope. API integration проверяет 202, 409, 422, SSE, filters, selection, undo и аудит сообщений. Старые тесты сохранены.

Chromium script `scripts/check_research_ui.py` работает с реальным сохранённым пулом: 1920×1080, 1440×900, 1280×800, 768×1024, 430×932, 390×844. Проверяет overflow, отсутствие supplier/stock/SKU на карточках, сохранение выбора после reload, filters/Escape и mobile chat. Артефакты `.local/research/ui`. Результат: 102 backend tests passed, 4 opt-in tests deselected; 14 новых research-тестов также отдельно прошли после исправления импорта facts. Chromium: 9 checks passed, page_errors пуст. Ruff, ESLint, production build и встроенный в build TypeScript check прошли.

## 27. Live: «синие бутылки, 300 шт.»

Бюджет 180 секунд, одна query expansion на категорию, все шесть источников. **Валидный уникальный пул: 8; полное покрытие: 0/6.** Два Portobello gift sets из первоначальных 10 результатов исключены повторной проверкой категории. Исходные наблюдения сохранены для диагностики, не показываются как подходящие бутылки.

| Источник | Страниц | Просмотрено | Принято наблюдений первоначально | Отброшено первоначально | Итоговый valid pool | Статус |
|---|---:|---:|---:|---:|---:|---|
| Oasis | 0 | 0 | 0 | 0 | 0 | FAILED HTTP |
| Ucontay | 1 | 94 | 0 | 94 | 0 | LIMITED |
| Gifts | 1 | 29 | 3 | 26 | 3 | LIMITED |
| Portobello | 1 | 27 | 2 | 25 | 0 | LIMITED; 2 audit exclusions |
| HappyGifts | 1 | 23 | 1 | 22 | 1 | LIMITED |
| ArteGifts | 1 | 19 | 4 | 15 | 4 | LIMITED |

Нулевой accepted не доказывает отсутствия подходящих товаров. LIMITED здесь связан с RESEARCH_TIME_BUDGET. Отчёт `.local/research/acceptance/bottles.json` содержит duration, IDs и детали веток.

## 28. Live: IT-конференция, 300 человек

Первый запуск около 190 секунд с бюджетом 180; затем выполнено продолжение с тем же бюджетом. **14 уникальных валидных товаров; полное покрытие: 0/6.** Есть завершённые отдельные ветки, но не источники целиком.

| Источник | Страниц суммарно | Просмотрено | Принято | Отброшено | Итоговый pool |
|---|---:|---:|---:|---:|---:|
| Oasis | 0 | 0 | 0 | 0 | 0 |
| Ucontay | 5 | 96 | 4 | 92 | 4 |
| Gifts | 4 | 43 | 3 | 40 | 3 |
| Portobello | 4 | 41 | 3 | 38 | 3 |
| HappyGifts | 4 | 34 | 0 | 34 | 0 |
| ArteGifts | 4 | 28 | 4 | 24 | 4 |

Oasis: SUPPLIER_HTTP_ERROR. ArteGifts: отдельная SUPPLIER_PARSING_ERROR. Остальное ограничено time budget/branch slice. Полные метрики `.local/research/acceptance/conference.json`; continuation не выдаётся за новый полный scan.

## 29–30. Производительность и ограничения

Запросы ограничены semaphore=2 и delay=0.8 секунды; страницы не запускаются десятками. Начальные результаты появляются до окончания исследования. Установленный в локальном `.env` курс RUB/KZT 5.2 сохранён без изменения; это configurable business rate, не online quote.

Ограничения: один in-process worker; JSON aggregate переписывается при checkpoint; SSE отправляет полные snapshots; semantic ranking и grouping эвристические; initial discovery словарная; часть фактов сайтов отсутствует; provider layouts могут измениться. Полная live полнота не доказана, Oasis сейчас блокирует критерий «работают 6 suppliers». Требуется повторная проверка после восстановления доступа и более длительный обход всех веток. CAPTCHA обхода нет. Никаких presentation/PDF/PPTX/image-generation возможностей не добавлено.
