# Search quality, state integrity and request safety

31 октября 2026. Итоговый статус: **PARTIAL**. Подтверждённые дефекты исправлены;
проверенные функциональные сценарии проходят. На 10 пользователях HUD не достиг
ориентира p95 ≤1 s. Ограниченный тест не доказывает универсальное понимание языка,
абсолютную безопасность или производительность всего рабочего каталога.

Машиночитаемые результаты: [summary.json](../apps/api/tests/quality/results/summary.json).
Исходные результаты, полные matching IDs, intents, seeds и измерения лежат рядом.
Единый постоянный harness: [check_search_safety.py](../scripts/check_search_safety.py).

## Контуры и воспроизведение

Исходный commit `83a4ddc0d7df33d7b903c2dd087b85551a5b4b1f`; дерево уже содержало
незакоммиченные изменения. Исходные пути и hash diff сохранены в
[environment.json](../apps/api/tests/quality/results/environment.json). Чужие
изменения не откатывались. Случайное форматирование ранее чистых файлов отменено
только после проверки их AST и исходного списка изменений.

Python 3.12.5, PostgreSQL 17.10, SQLAlchemy 2.0.52, asyncpg 0.31.0,
FastAPI 0.141.1, Pydantic 2.13.5, Next.js 16.3.4, Uvicorn 0.52.4.
Windows 11, 10 физических /16 логических CPU, 16.87 GB RAM. Зависимости не обновлялись.

| Контур | Данные и разрешённые действия |
|---|---|
| Unit/property | Fixtures, без внешнего HTTP; property worker с внешним watchdog 120 s |
| Integration/load/fault | Отдельный PG `55433`, настоящие Alembic-миграции, 179 размеченных предложений; отдельный API `18000` |
| Live | Основной PG `55432`, 4664 offers /1780 families в исходном snapshot; три изолированных аккаунта/проекта; только штатные действия |
| Browser | Установленный Edge, собственный профиль; desktop 1440×1000 и mobile 390×844 |

На основном PostgreSQL/Ollama не выполнялись fault injection и нагрузка. Обычный
поиск читает индекс; сайт VTO не получал fuzz/load-запросов. В изолированном
фоновом workload использовался fixture provider через настоящие queue/lease/
staging/checkpoint механизмы, без HTTP к поставщику. Case/allocator не возвращены.

Первоначальный бюджет: 5000 socket HTTP, 40 min на фазу, не более 10 пользователей,
RSS собственного API/PG ≤1800 MiB, свободная память ≥1024 MiB. После найденного
дефекта параллельной гидратации **до повторного прогона** отдельно разрешены ещё
32 локальных запроса только для одного Edge SSE/XSS сценария: фактически 5010/5032.
ASGI fault checks учитываются отдельно и не создают socket-нагрузку. Основной live
бюджет 60 запросов дополнен двумя штатными запросами: воспроизведение и проверка
реального SKU после исправления, всего 62. Нагрузочные ступени не расширялись.

## Первопричины и изменения

| Дефект | Первая точка потери смысла/состояния | Исправление |
|---|---|---|
| `power bank`, `USB flash drive`, составные категории | Лексическое разбиение оставляло часть категории кандидатом в модель | Защита известных многословных понятий и составных существительных до вывода модели |
| Melango на реальном каталоге давал 0 | Query Understanding заменял `рюкзак-мешок` merchandising-категорией поставщика «наборы» | Доказанное товарное понятие имеет приоритет над таким исправлением; имя Melango сохраняется |
| Количество, объём, размер, цена становились моделью или теряли оператор | Общий текстовый разбор не сохранял числовые роли | Типизированные branch constraints: inclusive/exclusive price/quantity, ml/mAh, size, exact SKU; противоречия отклоняются |
| Soft/negative фрагменты уменьшали pool или становились моделью | Identity извлекалась раньше области действия предпочтения/отрицания | Маскирование ролей; hard exclusion отдельно, soft влияет на порядок, без уменьшения eligible pool |
| Blue Lake превращался в BLUE-фильтр | Извлечение атрибутов из текста уже распознанного бренда/модели | Защищённый attribute_query сохраняет бренд/модель целиком, включая модель `42` |
| Дневник/блокнот, вечный карандаш, комплект/аксессуар смешивались | Слишком общая категория без required subtype/function | Подтип и функциональные признаки дополняют категорию; футляр не становится ручкой |
| Purpose после прежнего workspace терял quantity | Самостоятельная purpose-фраза попадала в refinement | Новый purpose-запрос заменяет workspace; отрицания не становятся ветками |
| Точный SKU после Tippo оставлял Tippo | SKU не содержал category/model токенов и попадал в no-op refinement | Самостоятельный exact SKU запускает NEW_SEARCH; постоянная HTTP-регрессия этой последовательности |
| «8 наборов: …» отбрасывал товары | Не распознавалась именная конструкция требования сборки | GRID сохраняет товарные ветки/локальные условия и сообщает об отсутствии автоматической сборки |
| Ошибка сериализации оставляла новый active workspace | Сохранение происходило до успешной подготовки ответа | Preflight JSON до короткой атомарной публикации research/query/messages/active pointer |
| Старый поиск/History мог активироваться последним | Отсутствовал общий порядок публикации медленных операций | Совместимая миграция `0023_request_epoch`, reservation + CAS; устаревшая публикация получает 409 |
| Архивный replay зависал | Повторный захват project lock | Подготовка новой GRID-сессии вне lock; архив не переписывается |
| Проектный lock удерживался при планировании | Session advisory lock и широкая транзакция | Transaction advisory lock; подготовка/LLM/сеть вне проектной транзакции |
| JSON/параметры/Origin проходили без общей границы | Проверка после дорогого разбора либо отсутствие проверки | ASGI boundary, ранние byte/depth/type limits, typed errors, cookie Origin policy |
| GET frontend с JSON header отклонялся | Пустой GET ошибочно считался JSON body | Тело разбирается только для операций с телом |
| Штатное открытие интерфейса получало 429 | Два параллельных чтения на пользователя были меньше фактической гидратации UI | Четыре чтения, две записи, общий лимит восемь; SSE имеет отдельные слоты |

Основные файлы: `domain/query_constraints.py`, `domain/catalog_identity.py`,
`domain/product_purpose.py`, `domain/query_understanding.py`,
`application/product_grid.py`, `application/project_queries.py`,
`database/research.py`, `database/connection.py`, `api/request_safety.py`,
`main.py`, `ai/local.py`, `api/project_queries.py`, `api/research.py`,
`workspace-queries.tsx`, `use-research.ts`, отображение ограничений HUD.
Остальной исходный dirty diff не приписывается этой задаче.

## Семантика и независимая оценка — PASS в размеченном universe

Корпус [dataset.json](../apps/api/tests/quality/dataset.json), версия
`search-safety-20261031-v1.1`, seed `20261031`. Истина — явно заданные атрибуты
179 fixtures и независимые правила membership; oracle не вызывает production
parser/resolver/SQL-builder. Проверяется объединение **всех** страниц по offer ID.

До исправлений: **113/150** исходных проверок. Ошибка разметки «ручка в футляре»
ожидала любую ручку вместо требуемого признака; исправленная оценка исходного
результата даёт 114/150. Для «бутылки с карабином» также уточнён требуемый признак,
но исходный результат всё равно был неверным. Исходный baseline сохранён.
Это не сравнение двух неизменных вселенных: добавлены 15 регрессий и исправлена
fixture NAVY, первоначально ошибочно нормализованная как DARK_BLUE.

После: **165/165**. Из них 133 непустые выдачи; по всему размеченному pool —
0 посторонних и 0 пропущенных IDs. Macro precision@10/@50 =1.0, recall известного
fixture universe =1.0, mean Jaccard к gold =1.0. Для precision знаменатель
`min(k, returned_count)`; пустые/отклонённые запросы не выдаются за precision=1.
Полный membership точен, поэтому сортировка не скрывает ложные совпадения.
Эти значения **не являются recall всего ассортимента VTO**.

| Пример на изолированном индексе | До, IDs count | После | Что проверяется |
|---|---:|---:|---|
| power bank | 0 | 2 | Целый концепт |
| USB flash drive | 0 | 1 | Накопитель, без USB humidifier/lamp/hub |
| GF-12449.40 | 0 | 1 | Exact SKU |
| Красные ручки ≥500 и синие бутылки ≥100 | 5 | 2 | Локальные AND, ветки OR |
| Повербанки 10000 mAh, 100 шт, ≤12000 ₸ | 2 | 1 | Разные числовые роли |
| Ручки желательно металлические | 0 | 143 | Soft не сокращает membership |
| Ручки без пластика | 0 | 137 | Hard exclusion |

Один и тот же model name двух брендов, multiword brand, бренд только в чужом
описании, accessory/packaging/set, multi-concept изделие, unknown brand/model,
строгие оттенки, eternal 2-in-1, дневник/блокнот и разные локальные ограничения
включены в корпус. Accuracy по классам находится в summary, а не только общий count.

Первый frozen holdout: **22/50** по проверяемому intent-контракту, хотя membership
50/50. Найдено создание лишней динамической ветки из «цена до…»; это не засчитано
как успех. После исправления второй holdout: **100/100**, seed `927652`.
[Входы H1](../apps/api/tests/quality/holdout.json) и
[H2](../apps/api/tests/quality/holdout-v2.json) сохранены отдельно. H2 содержит новые
числа/порядок в том же семействе шаблонов, **не независимую широкую unseen grammar**.

Property: **5000/5000**, 5000 уникальных входов, 0 duplicates, seed `20261033`.
Это numeric roles/schema/identity masking, не 5000 разных лингвистических задач.
Общий внешний watchdog ограничивает процесс; размер входов ограничен. Полная
доля false corrections/AMBIGUOUS для произвольного языка **NOT MEASURED**.

## Retrieval, наличие и рабочий индекс

Сохраняются полные matching IDs; UI page size =100. В fixture есть 130 изделий
Page Brand; проверка собирает все страницы без потерь и дублей. Source constraints
и HUD остаются раздельными. Строгий BLUE не включает LIGHT_BLUE/NAVY автоматически;
близкие оттенки разрешаются явно. Точная вместимость/размер требуют опубликованного
признака. Материалы сейчас используют группы METAL/PLASTIC/WOOD и т. п.; точный
сплав и полная новая онтология оттенков в этой задаче не реализованы.

LAST_KNOWN_STOCK_ALLOWED сохранён. Проверены старый положительный остаток,
новый ноль после положительного, NULL, INCOMING и parent-shared metadata.
`в наличии` означает опубликованный положительный stock без подстановки quantity=1;
«желательно в наличии» не сокращает pool. Возраст observations сам по себе не
исключает предложение. Размеры и остатки разных вариантов не суммируются.

Empty branch получает bounded diagnostic: category/identity/color/material/
price+technical/stock/quantity. Это диагностический SQL по индексу, без синхронного
crawl и без прежнего O(N²) path. Нулевой результат не объявляется отсутствием
товара у поставщика. Непустая соседняя ветка сохраняется.

Live: 60 штатных поисков, из них 35 после исправлений, затем воспроизведение и
повтор exact SKU. Ранние неудачные запросы не скрыты в счётчике. Проверялись
реальные parent names/brand metadata и exact IDs, а не только HTTP 202.

| Запрос / семейство на рабочем индексе | Фактический результат |
|---|---|
| Ручка шариковая Tippo / Tippo | Те же 6 IDs `1944499,1944501,1944503,1944505,1944507,1944509` |
| Рюкзак-мешок Melango | 4 IDs `1823107..1823110`; до исправления compound было 0 |
| Синие Melango ≥300 | Только `vto:1823109` |
| VORTEX | 2 IDs `1934569,1934571`, без Alvortex |
| повербанк / внешний аккумулятор / Power Bank | По 195, полные ID-наборы равны, Jaccard=1 |
| флешка / USB flash drive | По 3, ID-наборы равны; USB-увлажнители не включены |
| термокружка / термостакан | 55 /7, понятия не принуждаются к равенству |
| бренд Molti | 66; проверен metadata brand каждого результата |
| Для записи и напитков, чёрное, ≥100 | 91; цвет и quantity проверены по наблюдениям |
| GF-12449.40 после Tippo | NEW_SEARCH; только `vto:1823109` после исправления маршрутизации |

Полные IDs/latency в [live.json](../apps/api/tests/quality/results/live.json).
Один cold-like Tippo занял 7.0 s, повторные — около 1–1.7 s; управляемого
эксперимента cold/warm Qwen и cold p95 не было. Числа каталога относятся к snapshot,
а не к полному каталогу поставщика.

## Состояние, HUD, History, коллекция — PASS покрытых цепочек

200 HTTP-последовательностей на непустом отдельном PG, seed `20261032`:
158×9, 29×8, 13×5 логических действий; pagination subrequests считаются отдельно.
Всего 800 membership assertions. Добавлены 200 последующих HTTP-чтений сохранённой
истории после нагрузки. Эталон состояния — независимые ID sets, не production patch.

Реальная цепочка: три ветки ручки→бутылки→рюкзаки; три выбранных варианта из разных
страниц; «Красные»; «Оставь только Molti»; «Бренд не важен»; hide/show;
remove/restore; новый поиск; history activation; неизменный snapshot.
Коллекция сохраняется отдельно от search Undo. Foreign collection/document =404.
Старый full replace без версии и устаревшие операции не становятся lost update.

В настоящем Edge просмотрены финальные desktop/mobile screenshots, без
горизонтального overflow. Пройдены hide/remove/restore/F5 и сохранение трёх выбранных.
В отдельном browser proxy **все /api направлены на тестовый API**, только frontend
assets читаются с main frontend: старый callback уже закрытого SSE не восстановил
старую сетку; безопасный HTML-canary остался инертным. Подписки и proxy закрыты.
Полная матрица всех межвкладочных/смены проекта гонок в реальном DOM **PARTIAL**;
Node-регрессии проверяют также обратный порядок History responses.

## Входы, security и восстановление — PARTIAL по охвату

| Предел / политика | Значение |
|---|---|
| Query | 1–2000 chars, ≤8000 UTF-8 bytes, token ≤256, ≤48 separators |
| JSON | 64 KiB search; presentation JSON 256 KiB для прежнего snapshot-контракта |
| JSON complexity | Depth ≤16 до decoder; nodes ≤4096; duplicate fields/NaN отвергаются |
| Ветки | ≤12; это не top-N matching pool |
| Quantity / price | До 10,000,000 шт /1,000,000,000; bool/string в числовом HUD не приводятся молча |
| HTTP search / readiness deadline | 45 s /3 s; очередь входит в deadline |
| LLM | Общий timeout queue+attempts+schema; внешний search deadline также действует |
| Search slots | 8 process, 24 waiting, queue ≤3 s; user writes 2 /reads 4 |
| User rate | 120/min, authenticated user; X-Forwarded-For не меняет bucket |
| SSE | Отдельно 16 process, 2/user, без waiting queue |
| PG operation scope | SET LOCAL statement 12 s /lock 2 s; pool checkout 3 s |
| Uvicorn | HTTP concurrency/backlog 64; число workers не менялось |

Входы NUL/bidi/zero-width/surrogate, пустые/emoji-only, malformed/deep JSON,
отрицательные/огромные/дробные quantities, duplicate parameters, unknown owner/admin
проверены постоянными тестами. Ошибки типизированы 400/422/409/413/429/503/504,
с безопасными trace ID. Stack trace/SQLSTATE не попадают в UI. Origin проверяется
на фактическом local proxy; remote production origin нужно явно настроить через
`trusted_frontend_origins`. CORS не считается авторизацией.

SQL значения параметризованы; сортировки/категории берутся из серверных структур.
LLM не имеет SQL/shell/file/network инструментов или права назначать owner. Invalid
structured output не превращается в SQL. В ASGI/PG намеренно отключали model
evidence, material exclusion и ownership: независимые проверки **действительно
обнаружили** расширенный pool/чужой ответ. Тривиальные set-sensitivity поля старого
load result не являются доказательством этих runtime mutation tests.

21 post-failure/recovery canary в финальном fault harness, включая исходный и SKU
контроли. Для planning deadline, serialization 500, publication rollback 503,
statement/lock timeout, pool exhaustion, LLM deadline/invalid/queue full,
caller cancellation, late publication, history race, SSE full/reconnect,
worker exit=7 и восстановленных mutation controls после каждой проверки выполнены:
health, два точных пользовательских поиска, обе collection versions,
foreign access, прежний snapshot/preview hash, 0 idle-in-transaction,
0 advisory locks, 0 checked-out connections и 0 зависших research jobs.
Ожидаемые инъекционные 5xx не смешиваются с обычной нагрузкой.

При отмене asyncpg SQLAlchemy записал диагностический CancelledError при
закрытии соединения. Последующие canaries подтвердили освобождение pool/locks;
«отсутствие исключений в логах» не заявляется. Exhaustive SQL/XSS/Markdown/URL,
SSRF/path и indirect prompt-injection corpus **NOT TESTED**; это не security certification.
Полная матрица 0/1/limit−1/limit/limit+1 для каждого поля и история с десятками
тысяч запросов не проверены. Отдельные границы не выдаются за исчерпывающий fuzz.

## Нагрузка — функционально PASS, latency PARTIAL

Непустой fixture snapshot, отдельные аккаунты/проекты, уровни 1→5→10; 20 не
запускались. Для каждого класса/уровня n=100 завершённых операций, с проверкой
полного membership. Closed-loop clients; это не открытый поток с постоянным RPS.
POST и терминальный GRID считались совместно. p50/p95/p99/max сохранены в JSON;
эмпирический p99 при n=100 равен максимуму и не является SLA.

| Пользователи | Category p95/max, s | Model | Brand | Union |
|---:|---:|---:|---:|---:|
| 1 | .342/.739 | .170/.356 | .119/.153 | .333/.413 |
| 5 | .927/1.037 | .521/.571 | .524/.646 | 1.035/1.141 |
| 10 | 2.116/2.356 | 1.257/1.381 | 1.187/1.250 | 2.200/2.581 |

| Пользователи | Purpose p50/p95/max, s | HUD p50/p95/max, s |
|---:|---:|---:|
| 1 | .212/.368/.477 | .137/.295/.354 |
| 5 | 1.164/1.312/1.440 | .620/.910/1.049 |
| 10 | 2.177/2.807/3.248 | .998/1.727/1.782 |

Неожиданные HTTP failures/5xx и несовпадения результатов в этих нагрузочных
прогонах — **0**. Admission queue/rejections по окончании —0. Purpose здесь
детерминированный: **0 inference calls**; это не нагрузочный тест реальной Qwen.
HUD p95 на 10 пользователях превышает целевой 1 s. Нового измерения полной
нагрузки после изменения read admission не было: бюджет не расширялся ради метрик.

Во время основного прогона 59 обычных fixture refresh jobs завершились COMPLETED;
существующий PDF worker создал synthetic 23-page документ READY за 4.701 s,
peak worker RSS 83.38 MB. Его 23 страницы не объявляются визуально проверенными.
Две страницы отдельного canary PDF просмотрены; старый preview проверялся после
каждой ошибки. Реальные пользовательские PDF не перегенерировались.

Rolling samples последнего n≤2048: основная нагрузка SQL p95 .0165/max .170 s,
pool checkout p95 ≈9 µs/max .0694 s; server loop lag p95 .0211/max .0971 s.
Checkout включает создание соединения и не равен чистому queue wait. Полные
pool hold, CPU profile, lock-wait distribution **NOT MEASURED**.

Отдельная конечная характеристика 40 чтений /10 пользователей: p50 .794,
p95 2.391, p99/max 2.615 s, peak PG+API RSS 546,144,256 B, минимум свободной памяти
3152 MiB, максимум DB connections 11. RSS суммирует shared pages и относится
к этой характеристике, не к пику всех предыдущих прогонов.

## Release gates и сохранность

| Gate | Статус |
|---|---|
| Размеченный corpus /числовые properties /H2 | PASS: 165 /5000 /100 |
| Отдельная мигрированная PostgreSQL, stateful и fault canaries | PASS |
| Реальный HTTP и финальный SKU после модели | PASS; ранние ошибки учтены отдельно |
| Edge desktop/mobile, late SSE и inert HTML | PASS в описанном объёме |
| Backend suite | PASS: 529 passed, 1 opt-in deselected; 230.52 s, после последнего SKU-исправления |
| Последние request/brand/category regression tests | PASS: 72 |
| Frontend Node tests | PASS: 32 |
| Ruff /ESLint /TypeScript /production build /diff check | PASS |
| Ориентир HUD p95 ≤1 s при 10 пользователях | FAIL: 1.727 s |
| Реальная Qwen под смешанной нагрузкой, длительный load, 20 users | NOT TESTED |
| Полнота всего VTO, весь естественный язык, абсолютная безопасность | Не заявляются |

Фактический итог очистки и fingerprints:
[cleanup.json](../apps/api/tests/quality/results/cleanup.json).
Схема основной БД получила только совместимую миграцию `0023_request_epoch`.
Основные PG и Ollama не останавливались. Источник VTO остаётся в собственном
backoff с PENDING HTTP/not-found ошибками; задача не выдавала его за полностью
импортированный каталог и не сбрасывала jobs/checkpoints.

Очистка **PASS**: остановлены и удалены отдельный кластер 55433, его API и
принадлежащий задаче Edge profile; удалены 85 точно зарегистрированных временных
папок/файлов, private ledger и cookies. На основной БД удалены точные IDs трёх
тестовых проектов, трёх аккаунтов и трёх их документов, с проверкой ownership.
Все **8 остальных черновиков** сохранили прежние fingerprints. Основной каталог:
**4664 →4664**, 0 добавленных/изменённых/удалённых коммерческих записей;
SHA-256 до/после `2c643f055a5d9491604d6d04fc019bd25e1ce4a0a5ceb7428bde2f23735098de`.

Перед удалением тестовых аккаунтов прочитаны exact SKU `vto:1823109` и независимая
пустая подборка его проекта. После очистки основной API health, frontend и его
health proxy вернули **HTTP 200**. Основные API/frontend/PG/Ollama работают,
тестовые listeners закрыты. Рабочие service logs, VTO storage/cache, backups,
постоянные regression tests, datasets и sanitized results сохранены.

Промежуточные неудачные проверки также учтены: прежние тестовые ожидания
нестрогого BLUE и случайной ручки для travel-purpose исправлены под действующий
контракт; packaging plural получил отдельную регрессию. Windows MAX_PATH привёл
к ошибкам записи тестовых изображений — финальные pytest запускались в короткой
собственной папке. Слишком длинный параметризованный test ID заменён коротким ID.
После production build frontend штатно перезапущен для согласования chunks.
В browser harness исправлено ожидание включённой кнопки при пустом composer;
это ошибка проверки, а не новый UI-дефект. Все финальные gates выполнялись после
соответствующих исправлений; ни один такой ранний запуск не назван PASS.

## Запуск и источники

Из корня: `.venv\Scripts\python.exe scripts/check_search_safety.py <phase>`.
`prepare` создаёт новый собственный кластер и запрещает занятый порт; затем
`serve`, `corpus`, `holdout`, `properties`, `stateful`, `load`, `mixed`, `faults`.
`live`, `deploy`, `browser` — явные отдельные фазы; load/fault не выбирают main DB.
`summary` сохраняет sanitized evidence, `cleanup` проверяет точный ledger.
Повтор на существующем незавершённом ledger не означает новый чистый baseline.

Применены официальные документы под установленными версиями:
[SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html),
[PostgreSQL 17 timeouts](https://www.postgresql.org/docs/17/runtime-config-client.html),
[Uvicorn settings](https://uvicorn.dev/settings/),
[OWASP input validation](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html),
[SQL parameterization](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html),
[LLM injection](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html),
[object authorization](https://api-security.owasp.org/editions/2023/en/0xa1-broken-object-level-authorization/),
[resource limits](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/),
[stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html).
Next.js client-boundary docs прочитаны из установленного package; Hypothesis и
Chromium в проект не устанавливались.
