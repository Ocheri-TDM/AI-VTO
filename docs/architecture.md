# Архитектурные решения этапа 1

## Источник истины и модель

Сайты поставщиков — единственный источник товарных данных. `Product` представляет наблюдение конкретного цветового/ценового варианта в определённый момент, а не пожизненную запись каталога. Цена, остаток и цвет должны относиться к одному варианту. Неизвестный остаток — `None`, не «достаточно». Транзит и резервы нельзя прибавлять к свободному складу.

Поставщик отвечает за чтение собственного формата. Общие Currency/Color/Availability services применяются после extraction в `SearchService`. FX отсутствует в scraper. Провайдер не получает SQLAlchemy session. Добавление нового поставщика требует отдельного адаптера и регистрации в composition root `main.py`; SearchService не содержит условий Oasis/Ucontay.

## Асинхронное выполнение

POST создаёт PostgreSQL SearchSession и возвращает 202. Клиент опрашивает GET, видит supplier progress и результаты по мере завершения категорий. Поставщики выполняются через `asyncio.gather`, внутри одного поставщика термины идут последовательно. Ограничения: число активных поисков, число Chromium contexts, число страниц, кандидатов и общий timeout поставщика. Retry применяется только к transient supplier errors; CAPTCHA не повторяется.

В этапе 1 фоновые задачи находятся в одном процессе. Single-flight lock предотвращает дублирующий обход одинакового запроса внутри этого worker. Запуск в нескольких workers пока не поддерживается: для следующего этапа потребуется очередь/lease и распределённый lock. PostgreSQL хранит прогресс; при перезапуске зависшие сессии явно завершаются как interrupted. `CancelledError` вызывает закрытие browser context и фиксацию статуса.

## Хранение и TTL

Схема приложения отделяет Users/Projects/Chats/Messages от SearchSession/CachedProduct. Нормализованный intent, supplier statuses и semantic traces хранятся в сессии. Product payload хранится только в CachedProduct с FK `ON DELETE CASCADE`, своим fetched_at и expires_at. Периодическая очистка имеет минутную точность; чтение проверяет точный TTL независимо от cleanup. Возвращать устаревший остаток по fallback запрещено.

Фильтр сессии возвращает новое представление без мутации исходного набора и без обхода сайтов. Refresh создаёт новую сессию, сохраняя ещё действующий предыдущий снимок. По окончании TTL снимок удаляется; долгосрочные пользовательские проекты позднее должны хранить отдельно выбранные данные и требовать повторной верификации.

## Browser и наблюдаемость

Один Chromium process, отдельный ephemeral context на операцию поставщика. Контексты не используют существующий профиль браузера, cookies пользователя или сохранённую авторизацию. Навигация основного окна разрешена только на HTTPS-хосты адаптера. CAPTCHA — `SUPPLIER_CAPTCHA_REQUIRED`, без обхода. Внешние ресурсы страницы загружаются обычным браузером; приложение не использует приватные supplier API.

Сохраняются только семантические действия `OPEN_PAGE`, `SEARCH`, `OPEN_PRODUCT`, `READ_PRICE`, `READ_STOCK`, `READ_COLOR`, `READ_IMAGES`, `PAGINATE`, `ERROR`. Из URL в trace удаляются query string и credentials. Логи JSON включают supplier, длительность, число страниц, найденных/принятых/отклонённых товаров и контролируемые ошибки. Cookies, headers, raw page bodies и пользовательские запросы не логируются. Исследовательские HTML snapshots находятся только в игнорируемых `.local`/`.research`.

## UI и AI

ProductCard — отдельная публичная DTO, не включает supplier, stock, source_url и original_price. Details endpoint возвращает полное наблюдение. Next.js обращается к API через same-origin rewrite; адрес backend задаётся серверной переменной API_BASE_URL.

`ModelProvider.parse_intent` — единственная зависимость SearchService от разбора текста. Stage-one BasicIntentParser поддерживает четыре основные категории, текстовые цвета, тираж и простой бюджет за единицу KZT. Это ограниченный детерминированный parser, не заявленная полноценная LLM. LLM будущего возвращает валидируемый SearchIntent и не получает произвольного исполнения browser actions.

## Документация, использованная при проверке

- [Playwright Python: async API и Windows Proactor requirement](https://playwright.dev/python/docs/library).
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).
- [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html).
- [Embedded PostgreSQL: локальные бинарники для изолированной проверки на Windows](https://github.com/leinelissen/embedded-postgres). Это инструмент проверки в `.local`, не runtime dependency приложения.
