# Background Research — Stage 4

## Execution

BackgroundIndex содержит scheduler и worker host; IndexRepository предоставляет persistent queue/lease operations. API startup запускает asyncio tasks и сразу обслуживает запросы, не ожидая Chromium. Можно выключить host через BACKGROUND_RESEARCH_ENABLED, сохранив index query API. Business logic не зависит от cron.

Scheduler планирует каждый supplier отдельно: начальное смещение 600 секунд, коммерческий refresh 60 минут, discovery 24 часа. Просроченные jobs сохраняются в PostgreSQL. Шесть существующих providers используются без переноса site parsing в query service. Ошибка Oasis не отменяет задачи других suppliers.

Job types:

- CATALOG_DISCOVERY: обход seed/category queries и вариантов с естественным завершением pagination; сохранение cursor, без top-N. Текущая версия не гарантирует обход всего supplier category tree: новые необычные concepts поступают через targeted research.
- OBSERVATION_REFRESH: актуализация известных URL/вариантов. Ucontay grouping объединяет variant URLs одной карточки: один structured document обновляет все варианты.
- TARGETED_RESEARCH: дополнительные предметные запросы при пустом индексе или нехватке metadata. Существующие результаты возвращаются сразу.

Completion branch требует exhaustion и проверки reported total. Budget timeout сохраняет checkpoint и переводит job в PENDING для продолжения. Отсутствие новых товаров не доказывает полный каталог. Исчезновение URL приводит к ошибке/устареванию observation; автоматическое удаление товара по отсутствию на одной странице не выполняется.

## Reliability

Очередь использует SELECT FOR UPDATE SKIP LOCKED и условный UPDATE supplier lease. Два worker не получают один supplier sync одновременно. Lease действует job timeout + 60 секунд; после его истечения RUNNING восстанавливается в PENDING. При штатном shutdown checkpoint сохраняется. Эти операции дополнительно проверены на реальном PostgreSQL конкурентными claims. [Механизм row locks описан в PostgreSQL](https://www.postgresql.org/docs/current/explicit-locking.html).

Cooperative cancel проверяется между запросами; текущий запрос ограничен browser timeout. Supplier failures используют bounded backoff до часа; CAPTCHA не обходится и даёт паузу минимум час. Provider retry_after разделяется всеми job types, поэтому новый targeted query не обходит паузу. Timeout бюджета не трактуется как ошибка самого сайта.

Идемпотентный dedup_key предотвращает дубли одинакового supplier/type/query. Предложения upsert по supplier product ID. Pool/checkpoint сохраняются после каждого чтения; повтор после аварии безопасно обновляет observation.

## Resource limits and reuse

GLOBAL_RESEARCH_CONCURRENCY=2 задаёт количество workers host, PROVIDER_CONCURRENCY=1 — browser slots на источник, BROWSER_PAGE_LIMIT=2 — общий предел страниц. BrowserEngine переиспользует Chromium, анонимные supplier-scoped contexts/pages, вытесняет idle context при достижении лимита. CAPTCHA context закрывается. Отдельный процесс Chromium на товар не создаётся. Page reuse не отменяет чтение актуального supplier response.

Метрики jobs: pages_scanned, product_pages_opened, соответствующие duration_ms, products_seen/updated, errors, total duration. BrowserEngine отдельно считает network_requests и contexts_created/reused. Отчёты — `.local/stage4`.

## Configuration and launch

```dotenv
BACKGROUND_RESEARCH_ENABLED=true
OBSERVATION_REFRESH_MINUTES=60
CATALOG_DISCOVERY_INTERVAL_HOURS=24
GLOBAL_RESEARCH_CONCURRENCY=2
PROVIDER_CONCURRENCY=1
BROWSER_PAGE_LIMIT=2
RESEARCH_JOB_TIMEOUT_SECONDS=300
SCHEDULER_STAGGER_SECONDS=600
```

Также используются существующие RESEARCH_REQUEST_DELAY_SECONDS=0.8, RESEARCH_PROVIDER_DELAYS, PROVIDER_RETRIES, BROWSER_TIMEOUT_MS. Не ставить несколько hosts ради обхода provider lease: global concurrency сейчас относится к одному host, supplier lease действует через БД.

```powershell
.venv\Scripts\python.exe scripts/local_db.py start
$env:PYTHONPATH='apps/api'
.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
.venv\Scripts\python.exe -m app
```

Scheduler включён по умолчанию. Для воспроизводимых замеров используются `scripts/check_stage4_live.py`, `refresh_stage4_live.py`, `check_stage4_api.py`, `profile_stage4_providers.py`, `check_stage4_storage.py`. HTTP benchmark отключает worker и закрывает возможность browser вызова; данные перед этим поступают через реальный sync.
