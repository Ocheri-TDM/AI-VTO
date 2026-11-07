# Постоянное предложение проекта и ссылки на семь дней

> Исторический отчёт от 6 ноября. С 7 ноября новый срок — 14 дней; переход,
> постоянная переписка и отдельная навигация описаны в [chat-workflow.md](chat-workflow.md).

6 ноября 2026. **Подготовлено без входа в Vercel и без нового deployment**, по
последнему уточнению пользователя. Backend обновлён и проверен через уже
работающий `https://souvenir-studio-three.vercel.app`. Новый frontend собран и
проверен локально в установленном Edge. Это разные результаты проверки.

## Причины и исправления

**403 — подтверждённый отказ Origin.** Сессия через production proxy работала:
GET проектов возвращал 200. GET чужого проекта корректно возвращал 404.
POST `/api/projects/{owned_project}/offers` для своего тестового проекта
возвращал 403 с `ORIGIN_REJECTED`, `stage=origin`, request ID
`9b36f5cf2154421d9121cc4808605d80`. POST чата отклонялся тем же слоем.
Изначально backend доверял только двум локальным origins, публичный origin был
пуст. Это не предположение о CORS и не ошибка ownership или offer API helper.
[До исправления](screenshots/offer-lifecycle/before-403.json).

В существующей `.env` backend настроены точный production origin,
`PUBLIC_FRONTEND_ORIGIN` и Secure cookie. Сохранены auth, ownership, Origin/CSRF
и существующий same-origin Next proxy. Подмена forwarded headers с чужим Origin
по-прежнему даёт 403; другой пользователь получает 404. Успешный обычный вход
через HTTPS проверен HTTP и браузером, без переноса localhost cookie.
Cookie: host-only, Path=/, HttpOnly, Secure, SameSite=Lax.

**Дубликаты редакторов.** Кнопка каждый раз вызывала create; сервер всегда
вставлял новый Document. Теперь GET `/api/projects/{id}/offer` только читает
ссылку на основной документ: null означает подтверждённое отсутствие, ошибка
остаётся ошибкой. POST прежнего `/offers` атомарно создаёт либо возвращает
основной: 201/200. Используются существующая блокировка строки проекта и
частичный UNIQUE `uq_primary_commercial_offer`, а не общий UNIQUE для всех PDF.
Два реальных API-процесса с общей PostgreSQL вернули один ID.

Открытие, F5, подборка и повтор POST сохраняют документ, ручные поля, порядок,
исключения, материалы и публикацию. Изменение проектной коллекции не переписывает
draft. «Обновить состав из подборки» сначала показывает добавления/удаления;
подтверждённое изменение проверяет версии документа и коллекции, сохраняет
правки оставшихся позиций и ранее исключённые товары. Архивы доступны владельцу
для просмотра/готовых файлов; редактирование и новые renders направляются в основной.

**Бессрочные ссылки.** Раньше не было activated_at/expires_at. Теперь первый READY
атомарно включает доступ и фиксирует UTC-даты с разницей ровно 604800 секунд.
Очередь не расходует срок новой ссылки. Чтение, копирование, сохранение,
обновление публикации, retry и перезапуск срок не меняют. При `now >= expires_at`
API, PDF, XLSX и scoped assets отвечают 410 без содержимого; неизвестный/заменённый
ключ — 404. HTML содержит оболочку интерфейса; после 410 она показывает
«Срок действия ссылки истёк», без товарных секций. No-store/no-referrer/noindex
сохранены; фото обходят Next Image cache.

«Создать ссылку на 7 дней» после истечения — явное действие для того же документа:
новый токен/epoch, старый ключ больше не работает. Активная публикация обновляется
по прежнему URL и дедлайну. Обычные кнопки revoke/rotate убраны; внутренние
механизмы оставлены. Pending epoch и прежние claims защищают от позднего worker.
`commercial_offer_requests` — журнал идемпотентных действий, не новая очередь.
Он охватывает также публикацию уже READY private export: повтор старого UUID
после истечения не начинает ещё один период.

**Копирование и состояния.** Кнопка «Копировать ссылку» читает текущий полный
HTTPS URL и сообщает об успехе после writeText. При отказе видна ошибка и readonly
поле для ручного копирования. Локальный путь не предлагается вместо production URL.
Отдельно отображаются сохранение draft, подготовка файлов, готовность публикации
и срок с явным «UTC+5, Екатеринбург». На ошибке подготовки есть terminal status;
прежняя публикация остаётся доступной до своего дедлайна.

**Независимый VTO в товарном блоке.** Реальный VTOlogo.png замораживается отдельно
от клиентского логотипа. Web помещает его после галереи/полного описания, в нормальном
потоке снизу слева. PDF новых явных генераций использует offer_layout_version=2,
с сохранением завершающего текста вместе с логотипом. Старые PDF не пересоздаются.
Основной клиентский логотип, бренд/описание товара и собственные фотографии не
подменяются. SHA-256 файла VTO:
`3f95f55895a6741592d41da9624e737039bb9360b8fb65991264d67219f1e06d`.

Во время проверки двух публикаций выявлена отдельная Windows race: Path.resolve
иногда сохранял эквивалентный префикс `\\?\C:\...`, и guard ошибочно отклонял
собственный immutable logo. Теперь обычные extended drive/UNC формы нормализуются
после разрешения ссылок для root и target; проверка containment и запрет выхода
из storage остаются. Регрессия проверяет одновременные публикации и path traversal.

## Миграция и существовавшие документы

Применена совместимая `0025_offer_lifecycle`; повторный upgrade head не меняет
даты. Исходный HEAD `5b74ab101d886a2fc177b495ebdcb80caeb94740` (`commerce`).
Backend OpenAPI version 0.3.0, package 0.1.0; frontend package 0.1.0,
Next 16.3.4, React 19.2.8, Node 22.14.0. Новый cloud build не устанавливался.

Для проекта `cb386be6-8341-458f-80de-de9d9d358edd` существовали четыре предложения.
Сначала измерены состав, сохранения, ручные поля и публикации. Ссылки/ключи в отчёт
не включены. Правило выбора: непустой документ, наличие/число ручных изменений,
сохранённая версия, публикация, версия/время/ID. Это общее детерминированное правило.

| Документ | До | После |
|---|---|---|
| `3aafda97-a64c-4d82-b575-db5dbb9ab7ee` | v10, 6 позиций с ручными полями | Основной, PRESERVED_MANUAL_EDITS |
| `ceb04f4b-6ea5-4ed5-ac0d-ae94bb0779e1` | v1, 6 позиций, публикация | Архив, файлы/публикация сохранены |
| `dc004fca-848e-4819-9fb5-b6ac106a2468` | v1, 6 позиций, публикация | Архив, прежний owner URL сохранён |
| `7cbd78ab-46d8-4232-b5a4-48bbda6f9442` | v1, 6 позиций | Архив сохранён |

Для dc004… история единственного READY и время share совпали:
активация 06.11.2026 12:49:40.991417 UTC, истечение
13.11.2026 12:49:40.991417 UTC, CONFIRMED_FIRST_READY.
Для ceb04… достоверное первое READY-включение не установлено; безопасный fallback —
самый ранний render.created_at 06.11.2026 12:43:13.196393 UTC, истечение
13.11.2026 12:43:13.196393 UTC, EARLIEST_RENDER_CONSERVATIVE. Срок может быть
короче фактического на время подготовки; миграция не продлевает прежние ссылки
от своего запуска. Отозванные ключи не активируются. Draft, исходные timestamps,
ревизии, published IDs, файлы и encrypted tokens не переписываются этой миграцией.

## Проверки

| Проверка | Результат | Статус |
|---|---|---|
| Полный backend, включая прежние isolation/revision/concurrency suites | 593 passed, 1 opt-in deselected; финальный прогон 345.70 s | PASS |
| Lifecycle + прежние commercial regressions | 26 passed, 25.14 s; после усиления проверки добавления состава и scope VTO — 8 lifecycle passed, 13.06 s | PASS |
| Frontend suite | 64 passed | PASS |
| Ruff / ESLint / TypeScript / Next production build | Успешно | PASS |
| Существующий Vercel proxy → основная PostgreSQL | 21 HTTP-проверка, плюс обычный Secure login | PASS |
| Одновременное get-or-create двумя настоящими API-процессами | Один document ID, один primary в PostgreSQL | PASS |
| Перезапуск API | Тот же editor, URL, expires_at, anonymous read | PASS |
| Edge, Vercel owner/anonymous + новый локальный UI | 30 проверок: F5, две вкладки, read-error без fallback, копирование, uploads, download hashes | PASS |
| Desktop, Current/Pulse, mobile 390 px, editor и guest | Footer под секцией, без overlap/горизонтального overflow | PASS |
| Ровно до/после expiry, поздний worker, новый token, повтор UUID | Инъекция UTC-часов сервиса в постоянных тестах; системные часы/реальные ссылки не менялись | PASS |
| Истёкшая страница | Отдельная 410 UI fixture; товары отсутствуют | PASS для fixture |
| Новая frontend-сборка на Vercel | По просьбе пользователя подготовлена без авторизации/деплоя | NOT DEPLOYED |
| Native Excel / реальные семь суток / нагрузочный p95 / все браузеры / другое устройство или сеть | Не выполнялись | NOT TESTED |

HTTP: [до 403](screenshots/offer-lifecycle/before-403.json),
[после](screenshots/offer-lifecycle/http.json),
[Secure login](screenshots/offer-lifecycle/https-login.json),
[два процесса](screenshots/offer-lifecycle/cross-process.json),
[перезапуск](screenshots/offer-lifecycle/restart.json),
[Edge/геометрия](screenshots/offer-lifecycle/browser.json).
Clipboard проверен transport spy writeText и имитацией отказа; системный буфер
пользователя не читался и не изменялся. Отказ чтения primary проверен отдельной
browser fetch fixture; реальный backend дополнительно проверяет чужого владельца.

Скриншоты: [копирование и срок](screenshots/offer-lifecycle/owner-desktop.png),
[мобильный редактор](screenshots/offer-lifecycle/owner-mobile.png),
[VTO под товаром](screenshots/offer-lifecycle/product-desktop.png),
[мобильный товар](screenshots/offer-lifecycle/product-mobile.png),
[истечение, UI fixture](screenshots/offer-lifecycle/expired-ui-fixture.png).
Поле со значением capability скрыто только на screenshots; в интерфейсе оно
доступно для ручного копирования.

Контроль: [PDF](../apps/api/tests/fixtures/offer-lifecycle/offer.pdf),
[с отдельным клиентским логотипом](../apps/api/tests/fixtures/offer-lifecycle/offer-custom-header.pdf),
[длинное описание](../apps/api/tests/fixtures/offer-lifecycle/long-description.pdf),
[XLSX](../apps/api/tests/fixtures/offer-lifecycle/offer-custom-header.xlsx),
[manifest](../apps/api/tests/fixtures/offer-lifecycle/layout-manifest.json).
Три документа по четыре страницы: все 12 финальных PDFium-страниц просмотрены,
включая завершающий VTO после продолжения описания. Длинная fixture повторяет
исходный текст того же Melango до 6823 символов offline, каталог не меняет.
XLSX проверены openpyxl: числовые цена/тираж, cached ROUND, текстовый SKU,
защита листа, собственные thumbnails, отсутствие SUM/макросов/внешних связей.
В live примере 35224.51 × 101 = 3557675.51 KZT; web/PDF/XLSX совпали.

Промежуточные проверки выявили указанный Windows path race; он исправлен.
Первый тест трёх одновременных create получил штатный 429, тест изменён на два
процесса без ослабления rate limits. Новый frontend-тест бренда сначала имел
неполную fixture характеристик; исправлены данные теста. В новом browser script
исправлены async-generator assertion и проверка баннера через неверное поле:
баннер хранится как material, не settings.banner. Финальные прогоны выше успешны;
ранние неуспешные запуски не названы PASS.

## Производительность и сохранность

Контрольный трёхтоварный worker: assets 0.189 s, PDF 0.530 s, XLSX 0.135 s,
preview 0.198 s, всего 1.072 s; persisted created→READY 2.193 s.
Фото из существующего кэша, без Qwen/matting. Это smoke sample, не p95;
peak RSS в этой итерации не измерен. Workers/concurrency/retry budgets не увеличены.
Чтение публичного документа и повторное скачивание не запускают render.
[Измерение](screenshots/offer-lifecycle/performance.json).

[Сравнение с исходным SQL-снимком](screenshots/offer-lifecycle/preservation.json):
6634 SupplierOffer, 6634 ProductObservation, 6634 AvailabilityObservation — полные
row fingerprints совпали. Исходные поля 14 документов, все 16 ревизий и 6 прежних
collection items также совпали после исключения точных disposable IDs. Новые
primary/expiry поля — намеренные изменения миграции. Ключ шифрования сохранён;
известные старые PDF/fixtures не заменялись. Search/crawler/jobs/checkpoints
кодом задачи не сбрасывались. Активные сервисы и постоянный storage остаются.

## Что осталось сделать для frontend Vercel

Доступный HTTPS backend обнаружен через работающий локальный ngrok agent:
`https://resumptively-uncapsuled-rosemarie.ngrok-free.dev` → `localhost:8000`.
Сессии/точные тестовые ресурсы через существующий Vercel proxy читались из той же
проверенной PostgreSQL. Настройки аккаунта Vercel без входа не читались и не
менялись. Доступность зависит от компьютера, API, PostgreSQL, worker, storage
и tunnel; автономная работа при выключенном компьютере не заявляется.

Оба изолированных прогона очищены: [первый](screenshots/offer-lifecycle/cleanup-first-run.json)
и [мобильное дополнение](screenshots/offer-lifecycle/cleanup.json).
Всего удалены четыре disposable аккаунта, четыре проекта и три принадлежащих
им документа с render directories. [Финальная проверка](screenshots/offer-lifecycle/final-check.json)
подтвердила прежние полные fingerprints без исключений, ноль тестовых аккаунтов,
сохранённый ключ и HTTP 200 API/frontend/локального proxy/существующего Vercel proxy.
Временные API :8001 и browser :9226 закрыты, профиль Edge и secret ledger удалены.

1. Проверить изменения в репозитории, сохранить их в используемой production
   ветке. Секретные `.env`, ledger, cookies и storage не коммитить.
2. В существующем Vercel Project: Framework Next.js, Root Directory `apps/web`,
   Node 22.x, Install `npm ci`, Build `npm run build`, Output оставить стандартным.
3. В **Settings → Environment Variables → Production** проверить серверную
   `API_BASE_URL=https://resumptively-uncapsuled-rosemarie.ngrok-free.dev`.
   Это текущий измеренный backend origin, без `/api`; если tunnel сменится,
   сначала проверить новый реальный origin. Не указывать frontend/localhost.
4. На backend сохранить настройки ниже, выполнить `alembic upgrade head` штатным
   способом и перезапустить API после обновления кода. На этой машине это уже
   выполнено; `commercial-offers.key` не заменять.
5. Выполнить новый Production deployment нужной ветки. Одного изменения env
   недостаточно. Затем повторить обычный вход, Create/Open, save, publication,
   anonymous view, PDF/XLSX и визуальную проверку VTO на production-домене.

```dotenv
PUBLIC_FRONTEND_ORIGIN=https://souvenir-studio-three.vercel.app
TRUSTED_FRONTEND_ORIGINS=["http://127.0.0.1:3000","http://localhost:3000","https://souvenir-studio-three.vercel.app"]
AUTH_COOKIE_SECURE=true
```

Backend/storage остаются вне Vercel Functions. БД, `PRESENTATION_STORAGE` и
`keys/commercial-offers.key` резервируются согласованно. При потере ключа
существующие encrypted links дают понятную ошибку восстановления; новый ключ
вместо утраченного автоматически не создаётся.

Воспроизведение: `scripts/check_offer_lifecycle.py prepare` → `http` →
`scripts/check_offer_lifecycle_browser.py` → `scripts/check_offer_lifecycle_layout.py`
→ просмотр всех страниц → `race` → перезапуск API → `verify` → `cleanup`.
Основной `http` рассчитан на первый явный create; повторять всю цепочку нужно
с новым run после exact-ID cleanup. `peer` — только одноразовый HTTP-процесс
проверки PostgreSQL lock, без lifespan/scheduler/worker. Ledger находится в
игнорируемой `.local`, содержит секреты и не публикуется.
