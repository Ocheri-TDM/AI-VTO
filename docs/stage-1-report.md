# Отчёт: этап 1

Завершение проверки: **9 сентября 2026**. Рабочий курс, указанный пользователем: **1 RUB = 5,2 KZT**. Курс сохранён в локальном `.env`, а не в адаптерах.

## 1. Что создано

Работающее локальное приложение: FastAPI API, Playwright/Chromium Browser Agent, отдельные Oasis/Ucontay adapters, нормализованные наблюдения о вариантах товаров, фильтрация по тиражу/цвету/категории/бюджету, KZT, PostgreSQL cache и Next.js UI. Моков в рабочем поиске нет. Подготовлены ModelProvider и граница будущего PresentationEngine; полноценных LLM и генератора презентаций на этом этапе нет.

## 2. Архитектура

`SearchIntent → cache → SearchPlanner → parallel providers → extraction → domain normalization → stock/color/category validation → deduplication → SearchSession → API`.

Playwright находится в browser/providers, SQLAlchemy — в database, правила отбора — в domain/application. Добавление поставщика требует нового `SupplierProvider` и регистрации в `main.py`; менять SearchService не нужно. Данные каталога не импортируются постоянно. Подробности: [architecture.md](architecture.md).

## 3. Дерево файлов

```text
apps/api/
  app/
    ai/{base,basic}.py
    api/{routes,schemas}.py
    application/{cache,planner,search}.py
    browser/engine.py
    database/{connection,models,repository}.py
    domain/{errors,models,presentation,services}.py
    providers/base.py
    providers/oasis/{provider,normalization}.py
    providers/ucontay/{provider,extraction}.py
    config.py, main.py, __main__.py, observability.py
  alembic/versions/0001_application_cache.py
  tests/ + fixtures/oasis/ + fixtures/ucontay/
  pyproject.toml, requirements.lock, alembic.ini
apps/web/
  src/app/{layout,page}.tsx, globals.css
  src/components/             # sidebar, workspace, search, results, cards, details, progress
  src/hooks/use-search.ts
  src/lib/{api,format,types}.ts
  package.json, package-lock.json, next.config.ts
docs/providers/{oasis,ucontay}.md
docs/{architecture,stage-1-report}.md
scripts/{research_*,live_acceptance,check_web,local_db,prepare_local_postgres}.py
README.md, .env.example, docker-compose.yml
```

## 4. Endpoints

| Метод | URL | Назначение |
|---|---|---|
| POST | `/api/searches` | Создать поиск; 202 пока выполняется, 200 при готовом cache hit |
| GET | `/api/searches/{id}` | Получить сессию, карточки и supplier progress |
| GET | `/api/searches/{id}/products/{product_id}` | Полные данные конкретного варианта |
| POST | `/api/searches/{id}/refresh` | Новый обход с прежним intent |
| POST | `/api/searches/{id}/filter` | Отбор из сохранённой выборки без новых browser requests |
| GET | `/api/searches/{id}/trace` | Semantic BrowserAction trace |
| GET | `/api/health` | PostgreSQL и наличие настройки курса |

Swagger — `/docs`, OpenAPI — `/openapi.json`. Supplier failure возвращается внутри результата, а не HTTP 500. Ограничение активных поисков — 429, недоступная БД — 503, фильтрация ещё работающей сессии — 409, истёкший кэш — 410/404 после удаления.

## 5. OasisProvider

Chromium открывает сайт, использует наблюдаемую форму `/srch`, читает `data-catalog-product` и ссылки точных цветовых вариантов. Следует опубликованным pagination links. В карточке использует JSON-LD для цены и `data-product` для характеристик, складов, вариантов и исходных фотографий. Число кандидатов/страниц ограничено конфигурацией. [Наблюдения и источники](providers/oasis.md).

## 6. UcontayProvider

Использует наблюдаемый поиск `/collection/all` с `q` и параметрами обычной формы. Пагинация — ссылка `a.pagination-next`. Карточка содержит `data-product-json`; hydration удаляет атрибут из DOM, поэтому адаптер читает HTML ответа **навигации Chromium** и декодирует structured data. Каждый `variant_id` становится отдельным Product со своей ценой, цветом и изображениями. [Наблюдения и источники](providers/ucontay.md).

## 7. Остатки

- **Oasis:** `settings.productWarehouses.main.sizes[]`, строка с точным ID варианта, поле `stock`. Москва; резерв отображается отдельно и не прибавляется/повторно не вычитается. Transit/fabric исключаются. Суммарные остатки семейства/других размеров не подменяют остаток выбранного варианта.
- **Ucontay:** `variants[].quantity`, которое storefront выводит как «Доступно». На исследованном варианте оно совпадает с остатком за вычетом резерва. Warehouse fields сохраняются как metadata; ожидание/резерв не прибавляются.
- `AvailabilityService` принимает только числовой остаток `>= quantity`. Неизвестные значения не попадают в карточки. Цвет/цена/остаток должны относиться к одному варианту. Фото другого цвета не используется как primary fallback.

## 8. RUB → KZT

`CurrencyService` использует `Decimal`, конфигурационный курс и `ROUND_HALF_UP`. При курсе 5,2 цена 100,50 RUB станет 523 KZT. KZT не умножается на курс, итог округляется до целого тенге. Неизвестная валюта/отсутствующий курс не превращаются в фиктивную цену. В unit tests есть дробные границы округления и некорректные значения.

## 9. Кэш

PostgreSQL хранит SearchSession и связанные CachedProduct (JSONB). TTL — максимум 3600 секунд; ключ включает нормализованный текст, intent filters, поставщиков, курс и лимиты. Одновременный одинаковый запрос в одном worker использует существующую задачу. Refresh создаёт отдельную сессию. Filter работает с сохранённой выборкой и не изменяет её исходный снимок.

Точный срок проверяется при чтении. Cleanup раз в минуту и на старте удаляет истёкшие сессии и товары каскадом. Перезапуск API переводит прерванные задачи в конечный статус, чтобы UI не зависал в loading. Users/Chats/Messages/Projects существуют отдельно от временных результатов.

## 10. Живой acceptance и ограничения

Проверен исходный запрос: «Темно-синие термокружки, рюкзаки, ежедневники и ручки. Тираж 300 шт.»

Результат первого живого прогона 09.09.2026:

| Поставщик | Просмотрено страниц | Найдено вариантов | Принято | Статус |
|---|---:|---:|---:|---|
| Oasis | 28 | 24 | 16 | Частичный результат: затем HTTP 403 |
| Ucontay | 49 | 160 | 4 | Поиск завершён в заданных лимитах |
| Итого | 77 | 184 | **20** | `partial`, успешный HTTP-ответ |

У всех 20 карточек проверены целочисленные KZT, численный остаток от 300, тёмно-синяя группа и соответствие одной из запрошенных категорий. Повтор запроса вернул тот же session ID с `cache_hit=true`; фильтр до 10000 KZT выполнился по сохранённым данным. Итоговая найденная выборка содержит термокружки и рюкзаки; наличие подходящих ежедневников/ручек этот ограниченный прогон не подтвердил.

HTTP 403 Oasis не обходился. Реальный каталог может ограничивать доступ; CAPTCHA возвращается отдельной ошибкой, без автоматического решения. Search limit означает ограниченную выборку, не отсутствие товара во всём каталоге. Статусы/warnings видны в UI. Остатки и цены не являются резервированием и могут измениться после fetched_at.

Детальный машиночитаемый результат конкретного прогона: `.local/live-acceptance.json`. Это локальное свидетельство проверки, не источник данных runtime.

Дополнительные границы: single API worker, локальный доступ без auth; базовый ограниченный parser; текстовые цвета; консервативная группировка дублей без удаления вариантов разных поставщиков; UI selection живёт в текущем окне. Перед многопользовательским развёртыванием нужны отдельные worker/queue, изоляция пользователей и авторизация.

## 11. Проверки

**69 automated tests passed**, три живых теста отделены маркером `live` и не запускались в этом детерминированном наборе. Включены:

- CurrencyService, ColorNormalizer, CategoryMatcher, AvailabilityService, SearchIntent/Planner, cache keys;
- TTL boundary, физическая очистка/cascade, восстановление interrupted sessions;
- параллельные одинаковые запросы, bounded retry, CAPTCHA, partial supplier result;
- API search/details/filter/refresh, DTO без служебных полей, контролируемая недоступность БД;
- Oasis/Ucontay normalization на наблюдаемых fixtures;
- Chromium: challenge/allowlist, чтение structured Oasis, Ucontay hydration и ошибка второй страницы;
- настоящий PostgreSQL: JSONB, Decimal roundtrip, timezone, TTL и cascade.

Миграция Alembic применена к PostgreSQL 17.10; `alembic check` — без расхождений. Ruff, Next.js lint и TypeScript check прошли. Production-сборка Next.js прошла.

Отдельно `scripts/check_web.py` проверил реальный UI 1440×1000 и 390×844: 20 карточек из живой сессии, checkbox, вкладка выбранного, details dialog, Escape, ценовой фильтр, новая подборка. JavaScript page errors: 0; горизонтального overflow нет. Скриншоты: `.local/web-check/`.

## 12. Запуск Windows

В текущем workspace уже есть `.venv`, Chromium, portable PostgreSQL на `127.0.0.1:55432`, мигрированная БД и `.env` с курсом 5,2. Запуск из корня:

```powershell
.\.venv\Scripts\python.exe scripts/local_db.py start
.\.venv\Scripts\python.exe -m app
```

Во втором терминале:

```powershell
npm.cmd --prefix apps/web run dev
```

UI: http://127.0.0.1:3000, Swagger: http://127.0.0.1:8000/docs. Если сервисы уже запущены, второй экземпляр не нужен. API запускается без reload и нескольких workers, с Proactor event loop. Python-скрипт запуска БД не требует изменения PowerShell execution policy. Стандартная установка на другом Windows, Docker Compose и команды тестирования — в [README](../README.md).

## 13. Этап 2

Локальные LLM-адаптеры Ollama/compatible runtime; устойчивый worker с lease/recovery; handoff пользователю при CAPTCHA; auth, проекты и сохраняемый чат/выбор товаров; image color analysis, удаление фона и улучшенная дедупликация; повторная верификация выбранных цен/остатков; presentation skills, несколько вариантов PDF/JPG. PPTX не предусмотрен.
