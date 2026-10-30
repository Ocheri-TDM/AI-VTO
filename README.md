# Souvenir Studio — VTO

Каталог поступает только из **VTO.kz** через публичный Store API и HTTP карточек.
Chromium и Playwright для установки и работы не нужны.

Любой запрос возвращает одну товарную сетку. Несколько категорий объединяются
в порядке запроса с сохранением локальных условий. Автоматическая сборка Case /
Hard Request удалена из рабочего пути; старые записи доступны как архив.
Бренд, цвет, материал, размер, цена и тираж фильтруются отдельно от исходного
запроса. Выбор, история и Undo сохраняются.

Поведение, измерения и границы проверки: [unified product grid](docs/unified-product-grid.md).
Поиск основного изделия, фильтры над сеткой и отдельные выбранные:
[проверка поиска и HUD](docs/product-purpose-hud.md).

Из выбранных вариантов можно создать отдельную PDF-презентацию с палитрой,
необязательным логотипом и проверкой фотографий без фона:
[установка и использование презентаций](docs/presentations.md).

## Локальный запуск (Windows)

Из корня проекта после настройки `.env` и PostgreSQL:

```powershell
.\.venv\Scripts\python.exe scripts/local_db.py start
.\.venv\Scripts\python.exe scripts/local_llm.py
.\.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
Start-Process -FilePath "$PWD\.venv\Scripts\python.exe" -ArgumentList '-m','app' -WorkingDirectory "$PWD\apps\api" -WindowStyle Hidden
npm.cmd --prefix apps/web run dev
```

Открыть <http://127.0.0.1:3000>. API: <http://127.0.0.1:8000/api/health>.
Portable PostgreSQL использует `127.0.0.1:55432/souvenir`; Docker из
`docker-compose.yml` — порт 5432. Указать нужный адрес в `DATABASE_URL`.
Запускать один API-процесс: он содержит scheduler существующей durable queue.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./apps/api[dev]"
.\.venv\Scripts\python.exe scripts/setup_presentations.py
npm.cmd --prefix apps/web ci
Copy-Item .env.example .env
```

Настроить `DATABASE_URL`. Для локального Qwen настроить Ollama либо оставить
`LLM_BACKEND=basic`. Новый аккаунт создаётся через форму регистрации; пароля
по умолчанию нет.

## Импорт и обновления

Обычный запуск **никогда не сбрасывает БД**. Фоновый worker выполняет VTO sync,
сохраняя страницы, ожидающие карточки, external IDs и ошибки в PostgreSQL.
Суточный интервал задаётся `FULL_RECONCILIATION_DAYS=1` и
`INCREMENTAL_SYNC_HOURS=24`; возраст observations сам по себе не исключает товар.

Ручной запуск/продолжение через ту же очередь и supplier lease:

```powershell
.\.venv\Scripts\python.exe scripts/sync_vto.py
```

Если worker уже занят, второй процесс ждёт lease. Поиск читает текущий индекс
и не ждёт окончания импорта. HTTP-настройки: `VTO_PAGE_SIZE`,
`VTO_REQUEST_DELAY_SECONDS`. Прогресс: существующая страница
`/dev/index` / `/api/dev/index` при `DEBUG_AGENT=true`.

Результаты перехода и ограничения: [docs/vto-integration.md](docs/vto-integration.md).

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest apps/api/tests -q
.\.venv\Scripts\ruff.exe check apps/api/app apps/api/tests
npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web test
$env:API_BASE_URL='http://127.0.0.1:8000'
npm.cmd --prefix apps/web run build
.\.venv\Scripts\python.exe scripts/check_vto.py
.\.venv\Scripts\python.exe scripts/check_product_grid.py
.\.venv\Scripts\python.exe scripts/check_product_purpose.py
```

HTTP acceptance создаёт изолированные ресурсы и удаляет только их точные IDs.
Архивные отчёты о прежних поставщиках описывают историю проекта.
