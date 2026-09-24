# Souvenir Studio — AI Research System

Текущая версия — **Final Research Platform**. Запуск и актуальные ограничения:
[финальный технический аудит](docs/final-report.md), [архитектура](docs/final-architecture.md),
[проверки](docs/testing-final.md), [состояние поставщиков](docs/provider-status.md).
Применять миграции до `0007_taxonomy_research`. Описания этапов ниже сохранены как история.

## Stage 4: Background Research Index

Обычный chat теперь ищет в PostgreSQL Research Index и не ожидает Playwright. Обновлением занимаются persistent background jobs; target freshness — 60 минут. «Карандаши 300шт» имеет category `pencil`, неизвестные термины идут в generic text search, без fallback в бутылки. Research pool не обрезается top-N.

Перед запуском применить миграции до `0005_supplier_backoff`. Scheduler включён по умолчанию; BACKGROUND_RESEARCH_ENABLED=false оставляет только чтение индекса. Настройки перечислены в `.env.example`.

Документация: [background execution](docs/background-research.md), [Research Index/API](docs/research-index.md), [open taxonomy и root cause](docs/search-taxonomy.md), [измерения производительности](docs/performance-stage4.md). Live coverage остаётся частичным; Oasis возвращал HTTP-ошибку. Новая архитектура не объявляет неполный индекс полным каталогом.

Ниже сохранено описание предшествующих этапов. Для текущего chat path приоритет имеет Stage 4. Старый Stage 2 message contract доступен через явный `legacy=true`.

Локальное исследование каталогов шести поставщиков: Oasis, Ucontay, Gifts, Portobello, HappyGifts и ArteGifts. Новый ResearchService сохраняет найденные наблюдения, покрытие, курсоры обхода и историю уточнений. Через 60 минут данные становятся устаревшими, но исследование не удаляется. Старые SearchService и API этапов 1–2 сохранены.

Текущий интерфейс — Research Workspace: частичные результаты через SSE, динамические фильтры, выбор, похожие товары и продолжение исследования. Обход ограничивается временем и числом веток, а не количеством товаров. Статус COMPLETE относится только к действительно завершённой ветке. Live-проверки пока не подтверждают полное покрытие всех шести источников; Oasis возвращал HTTP-ошибку. Подробности: [отчёт Research](docs/research-report.md), [архитектура](docs/research-architecture.md).

## Запуск Research на Windows

После установки зависимостей по инструкции ниже, в PowerShell из корня проекта:

```powershell
.venv\Scripts\python.exe scripts/local_db.py start
.venv\Scripts\python.exe scripts/local_llm.py
$env:PYTHONPATH = 'apps/api'
.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
.venv\Scripts\python.exe -m app
```

Во втором терминале:

```powershell
npm.cmd --prefix apps/web run build
npm.cmd --prefix apps/web run start
```

Открыть http://127.0.0.1:3000. Использовать один процесс API: текущий worker работает внутри процесса. `RESEARCH_MAX_SECONDS=600`, `RESEARCH_SUPPLIER_CONCURRENCY=2`, `RESEARCH_REQUEST_DELAY_SECONDS=0.8`, `RESEARCH_BRANCH_SLICE_SECONDS=45`. Индивидуальные задержки задаются JSON-объектом `RESEARCH_PROVIDER_DELAYS`. `MAX_PAGES_PER_QUERY` и `MAX_PRODUCTS_PER_QUERY` действуют только в legacy SearchService.

## Историческая основа: этапы 1–2

## Границы этапа

Поверх этапа 1 добавлены structured Local LLM adapters, Orchestrator, typed tools, SearchCoverage, incremental search, постоянные фильтры/выбор и история отмены, Chat/Message/AgentExecution, SSE и frontend-чат. Рабочий курс RUB→KZT — **5.2**, задаётся через `.env`. [Подробная архитектура](docs/ai-orchestrator.md). Модель сменная; режим `basic` обозначает детерминированный fallback. Полноценный менеджер проектов и авторизация пока не реализованы.

## Архитектура

```text
apps/
  api/
    app/
      domain/         # Product, SearchIntent, цвета, наличие, валюты, дедупликация
      application/    # SearchService, Orchestrator, ToolRegistry, SessionTools
      providers/
        base.py       # SupplierProvider
        oasis/        # Неактивный, сохранён с тестами
        gifts/        # Активный Gifts.ru, точные варианты
        ucontay/      # Только особенности сайта Ucontay
      browser/        # Chromium, контексты, навигация, CAPTCHA, semantic trace
      database/       # SQLAlchemy, SearchRepository, ConversationRepository
      api/            # Pydantic DTO и HTTP routes
      ai/             # Local LLM, AgentDecision, ContextBuilder, prompts, fallback
      main.py         # DI, lifespan, очистка кэша
      __main__.py     # Windows-совместимый запуск
    alembic/          # Версионируемая схема PostgreSQL
    tests/            # Domain, API, cache, provider, browser, PostgreSQL tests
    requirements.lock
  web/
    src/app/          # Next.js app router
    src/components/   # Компоненты интерфейса
    src/hooks/        # Stateful chat, SSE; legacy search hook сохранён
    src/lib/          # API client и TypeScript types
docs/
  providers/          # Наблюдения о настоящих сайтах
  architecture.md
  stage-1-report.md
scripts/              # Воспроизводимое исследование и проверки
.env.example
docker-compose.yml
```

```mermaid
flowchart LR
  UI[Next.js] --> API[FastAPI]
  API --> Intent[ModelProvider / SearchIntent]
  Intent --> Search[SearchService]
  Search --> Cache[(PostgreSQL: TTL 60 минут)]
  Search --> Planner[SearchPlanner]
  Planner --> Gifts[GiftsProvider]
  Planner --> Ucontay[UcontayProvider]
  Gifts --> Browser[Playwright / Chromium]
  Ucontay --> Browser
  Browser --> Sites[Сайты поставщиков]
  Gifts --> Domain[Цвет / валюты / наличие / дедупликация]
  Ucontay --> Domain
  Domain --> Cache
```

## Запуск на Windows / PowerShell

**В текущей рабочей папке среда уже подготовлена:** `.venv`, Chromium и изолированный PostgreSQL в `.local` на порту `55432`; в `.env` записан согласованный курс `5.2`, схема БД применена. Не перезаписывайте этот `.env` примером. Для повторного запуска имеющейся локальной БД: `.\.venv\Scripts\python.exe scripts/local_db.py start`, остановка: `.\.venv\Scripts\python.exe scripts/local_db.py stop`. Далее достаточно `python -m app` через `.venv` и команды запуска frontend ниже. Portable-бинарники не входят в репозиторий; на другой машине используйте стандартную установку ниже.

Требуются Python 3.12+, Node.js 22 LTS+ и PostgreSQL 17 (локальная установка или Docker Desktop с Linux containers). Команды выполняются из корня репозитория. В PowerShell используется `npm.cmd`, поэтому менять execution policy для npm не требуется.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps/api/requirements.lock
.\.venv\Scripts\python.exe -m pip install -e apps/api --no-deps
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

В `.env` задайте согласованный рабочий `RUB_KZT_RATE`. Например, `5.50` — только иллюстрация формата, не утверждение об актуальном курсе. При пустом курсе товары с RUB-ценой исключаются с диагностикой `CURRENCY_CONVERSION_ERROR`; KZT обрабатывается независимо. `CurrencyService` использует `Decimal` и `ROUND_HALF_UP`, вывод всегда целочисленный.

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m app
```

Если PostgreSQL уже установлен, создайте БД и пользователя, пропишите `DATABASE_URL` в `.env` и пропустите Docker. Приложение не создаёт таблицы автоматически: сначала выполните Alembic.

Во втором терминале:

```powershell
Copy-Item apps/web/.env.example apps/web/.env.local
npm.cmd --prefix apps/web ci
npm.cmd --prefix apps/web run dev
```

Интерфейс: http://localhost:3000. API: http://127.0.0.1:8000. Swagger: http://127.0.0.1:8000/docs. OpenAPI: http://127.0.0.1:8000/openapi.json.

**Запускайте API через `python -m app`, одним worker и без `--reload`.** Launcher выбирает Proactor event loop на Windows, необходимый для subprocess Playwright. Перезапуск backend вручную; frontend поддерживает hot reload. Привязка API к `127.0.0.1` намеренная: это локальный MVP без аутентификации. Перед внешним развёртыванием нужны авторизация, изоляция пользователей, защищённый reverse proxy и отдельный worker.

## Использование

Введите: `Темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300 шт.`

Разбор выделит четыре категории, тёмно-синюю группу и минимальный остаток 300. Запрос по каждому поставщику идёт независимо и параллельно с другим. На сайт передаются отдельные поисковые термины по категориям. В карточки попадают только варианты с подтверждённым численным остатком не меньше тиража, подходящим цветом и вычислимой ценой KZT.

Запросы ограничены `MAX_PAGES_PER_QUERY` и `MAX_PRODUCTS_PER_QUERY`: это поиск кандидатов, не полный обход каталога. Отсутствие результатов в ограниченном поиске не доказывает отсутствие товара у поставщика. Поставщик может требовать CAPTCHA или перестать отвечать; API сохраняет результаты второго поставщика и возвращает статус первого.

Основная карточка: фото, название, цена KZT, цвет/категория, выбор. Источник, артикул и складские данные доступны отдельно в деталях. Изображения загружаются по исходным URL поставщиков, удаление фона не реализовано.

## API

| Метод | Путь | Поведение |
|---|---|---|
| POST | `/api/searches` | `{ "query": "...", "refresh": false }`; 202 для фонового поиска, 200 для готового кэша |
| GET | `/api/searches/{id}` | Сессия, карточки, состояние каждого поставщика |
| GET | `/api/searches/{id}/products/{product_id}` | Полные служебные данные наблюдения |
| POST | `/api/searches/{id}/refresh` | Новая сессия с прежним intent, обход без использования кэша |
| POST | `/api/searches/{id}/filter` | `{ "max_price_kzt": 10000, "colors": ["NAVY"] }`; фильтрация сохранённой выборки |
| GET | `/api/searches/{id}/trace` | Семантические действия агента без cookies/headers/query string |
| GET | `/api/health` | Доступность БД, активные поставщики и настройки |
| POST | `/api/chats` | Создание проекта и чата; `{ "project_name": "..." }` |
| GET | `/api/chats/{id}` | История и текущее состояние выборки |
| POST | `/api/chats/{id}/messages` | `{ "message": "Убери дороже 10000" }`; Orchestrator |
| POST | `/api/chats/{id}/messages/stream` | Те же действия с SSE progress |
| POST | `/api/chats/{id}/selection` | Ручной выбор через typed SelectInput |
| PATCH | `/api/chats/{id}/workspace` | Сохранённые filters/sorting/undo с проверкой state_version |
| GET | `/api/projects` | Проекты локального пользователя с активным chat ID и последней активностью |
| PATCH | `/api/projects/{id}` | Название проекта и client_name |
| DELETE | `/api/projects/{id}` | Удаление проекта и его диалогов |

`filter` возвращает представление исходной сессии и не изменяет её снимок. Поэтому можно отменять фильтр и повторно использовать ту же выборку. Фильтрация незавершённого поиска возвращает 409. Истёкшая сессия возвращает 410 до физической очистки, после удаления — 404. Ошибка одного поставщика не становится HTTP 500; перегрузка поиска возвращает 429.

## Кэш и данные

SQLAlchemy-модели: `User`, `Project`, `Chat`, `Message`, `SearchSession`, `CachedProduct`, `AgentExecution`, `SearchStateHistory`. Миграция `0002` расширяет существующие таблицы. `CachedProduct` имеет составной ключ с сессией, `fetched_at`, `expires_at`; payload — JSONB в PostgreSQL. Нет таблицы master-каталога. Chat хранит активную сессию; state/history позволяют фильтровать и отменять без удаления исходного pool.

Ключ включает нормализованный текст, категории, цвета, количество, бюджет, branding/attributes, набор поставщиков, курс и лимиты поиска. NAVY/DARK_BLUE эквивалентны при поиске. Параллельные одинаковые запросы в одном worker используют одну задачу. Готовые и частичные результаты повторно используются до истечения TTL; полностью неудачные запросы не закрепляются в кэше.

TTL не превышает 60 минут с создания сессии. Истёкшие данные никогда не выдаются как действующие; фоновая очистка каждые 60 секунд удаляет сессии и каскадно товары. При запуске также выполняется очистка, а прерванные задачи переводятся в конечный статус. Исторические сообщения после удаления сессии получают `search_session_id = NULL`.

## Проверки

```powershell
# Unit/API/repository tests, без живых сайтов
.\.venv\Scripts\python.exe -m pytest -c apps/api/pyproject.toml apps/api/tests -m "not live and not postgres and not browser" -q

# Дополнительно Chromium с детерминированными страницами/fixtures
.\.venv\Scripts\python.exe -m pytest -c apps/api/pyproject.toml apps/api/tests -m "not live and not postgres" -q

.\.venv\Scripts\python.exe -m ruff check apps/api
npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run build

# Отдельная уже мигрированная тестовая БД PostgreSQL
$env:TEST_DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/TEST_DB"
.\.venv\Scripts\python.exe -m pytest -c apps/api/pyproject.toml apps/api/tests -m postgres -q
```

Живые browser tests выделены маркером `live` и запускаются явно. Они обращаются к настоящим сайтам и зависят от сети, CAPTCHA и текущего каталога. Fixtures используются только тестами; рабочий pipeline не подменяет товары демонстрационными данными. Подробности исследованных источников — [Gifts](docs/providers/gifts.md), [Ucontay](docs/providers/ucontay.md), [Oasis, отключён](docs/providers/oasis.md).

## Локальная модель и диалог

В текущей рабочей папке portable Ollama и Qwen3 4B уже подготовлены в `.local`.
Запуск сервера: `.\.venv\Scripts\python.exe scripts/local_llm.py`.
Результаты живого диалога, ограничения и полный список изменений: [отчёт этапа 2](docs/stage-2-report.md).

Установите [Ollama для Windows](https://docs.ollama.com/windows), запустите сервер и загрузите выбранную модель. Например, для проверки можно использовать Qwen3; это настройка среды, а не зависимость бизнес-логики:

```powershell
ollama serve
# В другом терминале; если сервер уже запущен приложением Ollama, serve не нужен.
ollama pull qwen3:4b
```

В `.env`:

```dotenv
LLM_BACKEND=ollama
LLM_BASE_URL=http://127.0.0.1:11434
LLM_MODEL=qwen3:4b
LLM_TIMEOUT_SECONDS=60
LLM_STRUCTURED_RETRIES=1
MAX_TOOL_CALLS_PER_MESSAGE=5
AGENT_TIMEOUT_SECONDS=600
DEBUG_AGENT=false
```

Для другого локального сервера используйте `LLM_BACKEND=openai_compatible`, его base URL и имя установленной модели. Поддержка JSONSchema обязательна. Для работы без модели — `LLM_BACKEND=basic`. Перезапустите API после изменения настроек.

В одном диалоге: исходный запрос → «убери дороже 10 тысяч» → «только рюкзаки» → «добавь бутылки» → «выбери 5 лучших» → «верни предыдущий вариант». Только первый поиск и добавление непроверенной категории обращаются к сайтам. Выбор сохраняется сервером; чат восстанавливается после перезагрузки страницы. Undo хранит 20 последних изменений, TTL товаров — 60 минут.

Живая проверка API: `.\.venv\Scripts\python.exe scripts/check_stage2.py`.
Проверка frontend после неё: `.\.venv\Scripts\python.exe scripts/check_stage2_web.py`.
Скрипты сохраняют отчёт в `.local/stage2`; нужны работающие API/frontend и доступ к сайтам.

## Рабочее пространство — этап 3

Реализованы менеджер проектов, desktop shell из трёх зон, mobile drawers, серверные
фильтры/сортировка, chips, optimistic selection с rollback, отдельный выбранный pool,
summary, Undo и обработка истёкшей сессии. [Подробный отчёт](docs/stage-3-report.md)
и [план архитектуры](docs/stage-3-plan.md).

Новые API: GET `/api/projects`, PATCH/DELETE `/api/projects/{id}`,
PATCH `/api/chats/{id}/workspace`. Миграция не требуется: расширены существующие JSONB
state и DTO; таблицы проектов/чатов переиспользованы.

```powershell
$env:WEB_TEST_URL = "http://127.0.0.1:3000"
.\.venv\Scripts\python.exe apps/web/tests/workspace_browser.py
# Реальные поставщики и локальная модель, отдельная проверка:
.\.venv\Scripts\python.exe scripts/check_stage3_live.py
```

# Final Research Platform

Supplier-first background traversal, staged observation safety, conservative reconciliation,
durable research references, paginated public results, delta SSE and developer index diagnostics
extend the existing Stage 4 implementation. Full live catalog coverage is not claimed.

Current documentation: [Architecture](docs/final-architecture.md),
[Discovery](docs/catalog-discovery.md), [Workers](docs/background-workers.md),
[Semantic evaluation](docs/semantic-search.md), [Provider status](docs/provider-status.md),
[Performance](docs/performance-final.md), [Verification](docs/testing-final.md).

Local PowerShell startup (repository root; use separate terminals for API/web):

```powershell
.venv/Scripts/python.exe scripts/local_db.py start
.venv/Scripts/python.exe scripts/local_llm.py
$env:PYTHONPATH='apps/api'
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
.venv/Scripts/python.exe -m app
```

```powershell
npm.cmd --prefix apps/web run build
npm.cmd --prefix apps/web run start
```

Open http://127.0.0.1:3000. Background indexing starts independently when
`BACKGROUND_RESEARCH_ENABLED=true`. Set `DEBUG_AGENT=true` before API startup to
enable http://127.0.0.1:3000/dev/index. Ordinary chat queries never wait for crawling.
