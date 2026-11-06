# Коммерческие предложения — локальная приёмка 6 ноября 2026

Основной путь: «Открыть подборку» → «Создать предложение» → редактор →
«Создать ссылку для клиента». Клиенту доступны просмотр, печать, PDF и Excel.
Цена и тираж редактируются отдельно у каждой позиции. Стоимость позиции —
её цена × тираж; общей суммы, заказа, оплаты или резерва нет.

По уточнению пользователя используется предоставленный **VTOlogo.png**:
`apps/api/app/assets/presentations/VTOlogo.png`, SHA-256
`3f95f55895a6741592d41da9624e737039bb9360b8fb65991264d67219f1e06d`.
Байты совпадают с корневым файлом пользователя. Активный валидный клиентский
логотип имеет приоритет. Повреждённый выбранный файл блокирует публикацию с
объяснением. Оригинальный 251.png и логотипы прежних ревизий сохранены.

## Что переиспользовано

ProjectCollection остаётся источником выбора. Создание ждёт очередь selection
и проверяет collection_version под существующей блокировкой проекта.
Document/Revision, CAS, PostgreSQL advisory locks, durable claims, worker,
ограничения времени/памяти/очереди, storage, безопасные uploads и PDFium общие
с прежними документами. Новая независимая очередь не добавлена. Qwen, маттинг
и художественный DesignPlan в коммерческом предложении не вызываются.

Дизайнерские шаблоны, AI-планировщик, грамматика и UI физически перенесены в
`apps/api/app/legacy/presentation_designer/` и
`apps/web/src/legacy/presentation-designer/`. На прежних путях оставлены тонкие
адаптеры. [Карта, hashes и восстановление](../legacy/presentation-designer/README.md).
Старые записи не получили новый discriminator. Их GET/download, черновики,
assets и `#presentation=ID` сохранены. Основная кнопка создаёт новый редактор.

## Снимок и происхождение значений

Совместимая миграция **0024_commercial_offers** добавляет selection_context к
элементу коллекции и таблицу commercial_offer_shares. Таблицы документов,
индекса, истории и пользователей не переименованы и не очищены.

В момент первого добавления фиксируются применимые branch/global/additional
quantity, версия research/view и происхождение. «Больше 250» даёт 251;
несовместимый или недостоверный исторический контекст — 1 с предупреждением.
Идемпотентное повторное добавление контекст не меняет. Поиск quantity=None
сохраняет свою семантику; единица относится только к стартовому тиражу документа.

Снимок хранит точный offer/variant, SKU, собственные фотографии, полное описание,
характеристики, исходную цену и дату, availability evidence и provenance.
Используется точная KZT-цена варианта из observation; Decimal/HALF_UP до 0.01.
Ручные цена, SKU с пояснением и тираж изменяют только документ. Обновление
каталога автоматически его не переписывает. Явный refresh показывает изменения,
сохраняет ручную цену/SKU и тираж. Превышение известного остатка — предупреждение,
без подмены введённого количества и без обещания резерва.

Клиент получает отдельный allowlist DTO: название, реальный SKU, описание,
характеристики, цена предложения, тираж, стоимость позиции, короткое наличие и
локальные scoped photos. Исходная цена, overrides, складские числа/города,
offer/user IDs, observations, provenance и storage keys не передаются.
HTML преобразуется в текст без исполняемых тегов; XML/PDF-текст экранируется.
Цвет характеристики берётся у выбранного варианта, не из списка цветов семьи.

## Публикация и доступ

| Состояние / действие | Что видит клиент |
|---|---|
| Новый/сохранённый черновик | Ссылки нет, требуется доступ владельца |
| Приватные PDF/Excel | Подготовленные owner-only файлы; ссылки ещё нет |
| Явная публикация | После READY атомарно доступны снимок и оба файла |
| Правка черновика | Прежняя опубликованная версия |
| Обновление публикации | Прежняя версия до READY, затем новый снимок по тому же URL |
| Ошибка подготовки | Прежняя готовая публикация; видимая ошибка у владельца |
| Отзыв | Публичный API, файлы и images возвращают 404; frontend показывает недоступность без документа |
| Новая ссылка | Прежний ключ недействителен; новый открывает готовую версию |

Повтор request UUID возвращает ту же revision; retry использует её замороженные
inputs. Share epoch защищает от запоздалой публикации после отзыва/ротации.
Worker не может восстановить отозванную ссылку. Одновременные сохранения имеют
CAS; устаревшая вкладка получает 409. Запоздалые editor polls не затирают правки.
В SQLite, где FOR UPDATE не действует, CAS защищён локальной блокировкой;
PostgreSQL сохраняет блокировку строки и параллельность разных документов.

Ключ — 32 случайных байта, URL-safe. БД хранит SHA-256 для поиска и Fernet ciphertext
для восстановления того же owner-link после перезапуска. Ключ шифрования:
`PRESENTATION_STORAGE/keys/commercial-offers.key`. Он создаётся атомарно один раз.
Резервировать **БД + storage + этот ключ** вместе; не удалять его при deploy/cleanup.
Ключи и cookies не выводятся в acceptance artifacts; Uvicorn access logging отключён.

Публичный namespace только `/api/public/offers/{key}`: read-only GET, собственные
ограничения частоты/конкурентности, без обхода auth других маршрутов.
Каждый file/image GET повторно проверяет ключ, active и published revision.
Скачивания привязаны к revision: после обновления старый pin возвращает **409
REVISION_CHANGED**, UI предлагает обновить страницу. Заголовки — private/no-store,
no-referrer, noindex/nofollow/noarchive, nosniff. Фото обходят общий Next image cache.
Уже скачанный клиентом файл отзыв с его устройства не удаляет.

Пользователь выбрал **только локальную проверку**. `PUBLIC_FRONTEND_ORIGIN` пуст:
редактор показывает относительный local preview path и объясняет ограничение.
Внешняя ссылка не составляется из Host или localhost. Для развёртывания нужен
доверенный `https://` origin frontend; затем перезапуск API. Внешние HTTPS/DNS,
доступ из другой сети и production proxy — **NOT TESTED**.

## Web, PDF и XLSX

Общий ClientOffer/OfferView используется в preview и публичной странице.
Оформление Pulse/Geist независимо от темы посетителя. На титуле баннер,
обязательный логотип и название; товары идут далее. Галерея не подставляет фото
другого варианта при ошибке. Неизвестные изображения дают placeholder и отдельное
предупреждение владельцу. Upload PNG/JPEG/WebP использует прежние лимиты и worker;
неудачная замена не уничтожает предыдущий готовый материал.

PDF — статический ReportLab, A4 portrait, embedded Geist с Noto fallback для ₸.
PDFium последовательно проверяет и растеризует каждую страницу. Длинные описания
продолжаются на следующей странице; стоимость позиции повторно не начисляется.
XLSX — настоящий workbook, embedded thumbnails, текстовые SKU, числовые цена и
тираж, формула `ROUND(G*H,2)` с сохранённым вычисленным результатом. Нет общей
SUM-строки, макросов, внешних связей, скрытых закупочных колонок или импорта.
Ячейки заблокированы; защита листа не является криптографической защитой файла.
PDF/XLSX содержат одинаковый snapshot hash / revision и выдаются из READY worker.
GET и кнопка обычного повторного скачивания не запускают новый render.

## Реальная проверка

Два disposable пользователя, один изолированный проект, заполненная PostgreSQL.
Выполнена последовательность поиска/выбора через текущий HTTP API:

| Вариант | Исходный запрос | SKU | Начальный тираж | Цена VTO, KZT |
|---|---|---|---:|---:|
| VORTEX vto:1934569 | ручки от 250 шт | HG-40404/30 | 250 | 1034.00 |
| Melango, синий vto:1823109 | рюкзаки от 100 шт | GF-12449.40 | 100 | 2084.00 |
| FLOW vto:1883738 | бутылки | HG-40331/08 | 1 | 1623.00 |

В документе цена Melango вручную изменена на **35224.51**:
**35224.51 × 100 = 3522451.00**. API, web, PDF и cached numeric XLSX совпали.
FLOW имел известный нулевой остаток: «Нет в наличии», а не фиктивную доступность.
Каталог и коллекция от ручных правок не изменились.

- [HTTP evidence](screenshots/commercial-offers/http.json): 35 проверок, включая
  owner/foreign/anonymous, приватный экспорт, snapshot/CAS, публикацию, старый pin,
  отзыв/ротацию и реальный render/download через архивный дизайнер.
- [Edge evidence](screenshots/commercial-offers/browser.json): основной create,
  250/100/1, autosave/F5, порядок, client preview, upload logo/banner, чистый
  incognito без cookie, desktop/mobile 390 px, обе темы, отсутствие overflow,
  печатные CSS, настоящие PDF/Excel downloads и совпадение hashes, видимый 409,
  отзыв и новый ключ. Это DOM и реальные HTTP, не только SSR.
- [Перезапуск/сохранность](screenshots/commercial-offers/restart-preservation.json):
  тот же owner-link после restart, анонимный доступ и прежние fingerprints.
- [Контрольный PDF](../apps/api/tests/fixtures/commercial-offers/offer.pdf),
  [XLSX](../apps/api/tests/fixtures/commercial-offers/offer.xlsx),
  [manifest](../apps/api/tests/fixtures/commercial-offers/manifest.json).
  Все четыре финальные PDFium-страницы просмотрены визуально.
- [Длинное описание](../apps/api/tests/fixtures/commercial-offers/long-description.pdf):
  отдельный offline layout fixture на реальном фото/SKU и 4548 символах повторённого
  исходного текста. Каталог не изменялся. Просмотрены все три страницы, продолжение
  читаемо, стоимость показана один раз; XLSX сохраняет полный текст по continuation rows.

Нативный Microsoft Excel в окружении не найден: **NOT TESTED**. Настоящий XLSX
прочитан openpyxl в formula/data-only режимах; содержимое ячеек и встроенные фото
визуально отображены в Edge. Это не называется проверкой native Excel UI.
У трёх live-вариантов по одному опубликованному фото: интерактивное переключение
нескольких ракурсов в этой выборке **NOT AVAILABLE**; состав/primary и missing-image
семантика покрыты изолированными регрессиями. Дополнительно в Edge проверены
галерея и раскрытие длинного описания на UI-only fixture: одна реальная фотография
того же варианта повторена для двух controls, исходный текст повторён для длины.
Это не утверждение о двух опубликованных ракурсах в каталоге; БД не изменялась.

## Референсы и границы

В текущем attachment есть только текст. «ТехноGrad. подарки.pdf», настоящий XLSX,
скриншот и конкретный URL предложения отсутствуют. Главная ohmygift.ru не дала
доступного предложения для изучения кнопок. Их точное устройство **NOT VERIFIED**;
реализован явно подтверждённый контракт, без заявления pixel match или изучения
недоступного Excel. Отсюда явный fallback A4 portrait и перечисленные XLSX-колонки.

Это ограниченная локальная приёмка; internet deployment, native Excel, длительная
нагрузка/p95, полный keyboard/screen reader аудит и все браузеры **NOT TESTED**.
Политики stock, crawler, поиск, авторизация и основная PostgreSQL не сбрасывались.
Результаты финальных quality gates и удаления disposable IDs дописаны ниже.

## Воспроизведение

Установить зависимости `pip install -e "./apps/api[dev]"`, выполнить
`alembic -c apps/api/alembic.ini upgrade head`, собрать frontend и перезапустить
API/frontend. Повторная установка rembg/Qwen для новых предложений не требуется;
они остаются зависимостями архивного дизайнера.

Acceptance: `scripts/check_commercial_offers.py prepare` → `run` →
`scripts/check_commercial_offers_layout.py` →
`scripts/check_commercial_offers_browser.py` → `race` → перезапуск API → `verify` → `cleanup`.
Ledger `.local/commercial-offers-ledger.json` содержит cookies и ключи, не печатается
и не коммитится. Cleanup удаляет только проверенные ID этого run и их document
directories; общие logo/photo cache, ключ шифрования и пользовательские assets остаются.

## Финальные результаты

| Проверка | Фактический результат | Статус |
|---|---|---|
| Полный backend, включая прежние routing/revision/concurrency suites | 585 passed, 1 opt-in deselected; 256.72 s | PASS |
| Новые коммерческие регрессии | 18 passed; invalid price/SKU, 251 strict quantity, additional/global/local quantity, XSS, full-text continuation, exports, failed update/retry, два цвета семьи, own photos, unknown/shortage, права и revoke/CAS | PASS |
| Frontend suite | 62 passed, включая 5 новых offer checks | PASS |
| Ruff / ESLint / TypeScript / Next production build / diff --check | Успешно | PASS |
| Основной HTTP API и populated PostgreSQL | 35 проверок | PASS |
| Отдельные одновременные HTTP-запросы к PostgreSQL | 7 проверок; CAS 200/409, один job при double publish, revoke во время worker | PASS |
| Edge owner/incognito, desktop/mobile | 24 проверки; исключение только из draft, архивный editor/preview, отдельная fixture для gallery/expanded description | PASS |
| Визуальный контроль PDF | 4 итоговые страницы + 3 страницы continuation fixture | PASS |
| Реальный XLSX / native Excel | Структура, cached values, типы, formula, protection и photos проверены; native UI не установлен | PARTIAL |
| Внешний HTTPS и недоступные референсы | Не проверены | NOT TESTED / NOT VERIFIED |

Промежуточные прогоны выявили неподходящую подложку непрозрачного VTOlogo.png
и сбой конкурентной публикации в SQLite-регрессии. Подложка исправлена; SQLite CAS
защищён отдельно от PostgreSQL. Конкурентный сценарий прошёл восемь повторов,
последний полный suite и реальную PostgreSQL-проверку. Неуспешные промежуточные
запуски не посчитаны PASS. В постоянном тесте сохранена диагностика HTTP-ошибок.

Измерение одного трёхтоварного экспорта: HTTP submit→READY **2.890 s**;
worker assets **0.900 s**, PDF **0.512 s**, XLSX **0.127 s**, preview **0.178 s**,
всего **1.750 s**; peak task-process-tree RSS **76.56 MiB**. Это smoke measurement,
не p95 и не измерение всей нагрузки системы. Лимиты/число workers не повышались.
Фотографии использовали существующую подготовку оригиналов; matting/AI не вызывались.

До/после — **6634 SupplierOffer**, одинаковый commercial fingerprint
`f49f58947522179478dc4e7ed04dfa92`. Все 6634 ProductObservation и 6634
AvailabilityObservation имеют исходные row fingerprints. Прежние **10 документов /
13 ревизий** также совпали с baseline; 127 контрольных assets/PDF/dependency files
сохранили hashes. Четыре прежних пути коду намеренно стали archive adapters.
Пользовательские файлы и документы не удалялись; jobs/checkpoints не сбрасывались.

[Основная очистка](screenshots/commercial-offers/cleanup-primary.json) удалила
по точным ID два disposable аккаунта, один проект и 11 созданных документов с их
render directories. Затем выполнены свежий prepare/cleanup и повторная полная
HTTP/browser/SQL цепочка самостоятельных scripts с собственным baseline:
[повторная очистка](screenshots/commercial-offers/cleanup.json). Ledger/cookies,
temporary Edge profiles, probes и font-build directory удалены. Постоянные tests,
контрольные файлы, общие assets и encryption key сохранены. После очистки API,
frontend и его health proxy возвращают 200; PostgreSQL, Ollama, API и frontend
работают, listener browser debugging закрыт.
Итоговая SQL/health/cleanup/hash-проверка:
[final-check.json](screenshots/commercial-offers/final-check.json).
