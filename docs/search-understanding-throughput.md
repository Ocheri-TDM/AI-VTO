# Поиск и наполнение VTO — 7 ноября 2026

**Поиск: PASS для проверенного набора. Импорт: PARTIAL, продолжается.**
Исправления внесены в существующие GRID, PostgreSQL-индекс, очередь и supplier lease.
Case assembly не возвращался. Прямой доступ к базе VTO не получен.

## Причины регрессии поиска

`Raw query → syntax normalization with offsets → QueryUnderstanding → identity/roles
→ GRID branches → SQL OR across branches / AND within branch → purpose/model checks
→ persisted research → HUD`.

1. В управляемой таксономии отсутствовали `cap` и `baseballcap`. Каталог знал
   навигационную фразу «кепки и бейсболки», но это не делало её товарным концептом.
2. Остаточный noun phrase «бейсболка» попадал в global model при наличии другой
   распознанной категории. Поэтому B получал нулевой общий pool.
3. Точки разрывали `тираж. 200`, прилагательные и многословные категории;
   fallback создавал dynamic branches из синтаксических фрагментов.
4. Общий начальный цвет и конечный тираж связывались с отдельными позициями.
5. Fuzzy vocabulary из широкого supplier label «папки с блокнотом» мог исправить
   правильное «блокноты» в другую категорию. Также cue «марк» совпадал с «маркеры».

Теперь категория защищается независимо от количества товаров в локальном индексе.
Добавлены два реальных headwear-концепта с отношением baseballcap → cap;
бейсболка входит в широкую кепку, крышка бутылки и рисунок кепки — нет.
Разбор неизвестного элемента списка сохраняет unresolved branch/предупреждение.
Явная неизвестная модель остаётся HARD; известные Tippo, Blue Lake и SKU сохраняются.
Защищены spans всех категорий, а не только нескольких специальных слов.

Начальный общий цвет применяется ко всем перечисленным товарам, если нет разных
локальных цветов. «Красные ручки, синие блокноты и чёрные кепки» сохраняет три
локальных цвета. Один общий тираж в начале/конце списка не подменяет несколько
позиционных требований. `Блокноты` используют NOTEPAD subtype существующего
notebook-концепта; запрос ежедневника не переименован в блокнот.

Нормализация хранит исходный текст и отображение offsets. Проверены десятичные
запятые, `GF-12449.40`, USB-C, 64 ГБ, 0,5 л, отрицания и мягкие пожелания.
Произвольное удаление пунктуации не используется. Вместо лимита 48 разделителей —
ограничения bytes/chars/tokens, длины punctuation run и количества branches.

## Реальные A–D и сохранённые ревизии

Выполнены отдельно и после `ручка Tippo`, через основной HTTP API с заполненной
PostgreSQL. После исправления все восемь запросов имеют четыре branches,
RED / minimum quantity 200 у каждой, без global model/brand.
Проверено равенство persisted intent и ответа, всех страниц pool,
A=C, B=D и fresh=after-Tippo по **полному множеству offer IDs**.

| Запрос | До: categories / model / pool | После |
|---|---|---|
| A | pen, notebook, thermomug / нет / 87 | pen, notebook, thermomug, cap / 34 |
| B | те же 3 / `бейсболка` / 0 | pen, notebook, thermomug, baseballcap / 33 |
| C | те же 3 / нет / 39 | как A, полное множество IDs совпадает |
| D | 3 товарных + 4 dynamic-фрагмента / нет / 48 | как B, полное множество IDs совпадает |

Полные before/after intents, IDs, количество результатов и проверки:
[до](screenshots/search-understanding-throughput/controls-before.json),
[после](screenshots/search-understanding-throughput/controls-after.json).
Первое промежуточное исправление сохранено отдельно как
[controls-after-initial.json](screenshots/search-understanding-throughput/controls-after-initial.json):
оно ещё не включало baseballcap в широкий cap. Финальная приёмка использует
окончательное исправление. Между начальным и финальным HTTP-снимками каталог
пополнился; изменение общего количества не приписывается исключительно парсеру.
GRID — фактический путь; API публикует `source_mode=index`, без прежнего Case mode.

## Ошибка сохранения

Подтверждён `POST /api/projects/{id}/collection/items`, **429 USER_REQUEST_LIMIT**,
stage admission, например trace `c04199fd6fb24bacbc66dc58862b3a4d`.
Чтения hydration занимали общий active counter, который write-запрос сравнивал
с меньшим write-limit. Пять чтений и одно сохранение воспроизводили проблему,
хотя минутный лимит не был достигнут.

Read/write slots разделены, ожидание ограничено. Global concurrency 8,
read 4 / write 2 и rate 120/min сохранены. Ответы 429 имеют Retry-After.
Frontend объединяет одновременные reload коллекции; только идемпотентные
add/remove допускают один повтор по Retry-After. Clear/CAS и старый проект
автоматически не повторяются.

Одинаковый 18-request сценарий: **4 ошибки до, 0 после**:
[до](screenshots/search-understanding-throughput/admission-before.json),
[после](screenshots/search-understanding-throughput/admission-after.json).
Это подтверждённая backend admission-причина; Vercel/proxy/provider limiter
в локальном воспроизведении причиной не были.

## Постоянные корпуса и приёмка

| Проверка | Результат |
|---|---|
| Независимые gold labels | 250/250; 38 реальных концептов, включая два headwear |
| Отдельный holdout, первый запуск | 270/300: ошибка scope в «не белые, красные» |
| Holdout после исправления scope | 300/300; это replay, не новый unseen score |
| Property inputs | 5000 уникальных, 5000 прошли; 5 hostile inputs отклонены |
| HTTP sequences на непустой отдельной PostgreSQL | 100 × 10 логических действий, 1328 HTTP |
| Native Edge | A–D, desktop 1440×1000 и mobile 390×844, selection/F5/HUD |
| Изолированная PostgreSQL, VTO fault regressions | 17/17 |
| Чистая цепочка Alembic на отдельной PostgreSQL | head 0025; основная БД не мигрировалась |

В 250 labels: canonical/global/sentences/punctuation/newlines/soft по 38,
плюс 22 regression. Проверяются concepts, отсутствие ложной модели, цвета и тираж.
В этих проверках нет необоснованных отказов или подмены известной категории.
Это не оценка всех возможных смыслов русского языка. Storage/USB, отрицания,
неизвестные модели/атрибуты и локальные цвета имеют отдельные регрессии.
38 концептов не дополнены вымышленными IDs ради квоты 40.

Property-проверки ограничены normalization/offsets/idempotence/constraints;
они не названы 5000 независимыми semantic tasks. Есть отдельный 120 s watchdog.
HTTP sequences проверяют точные независимые fixture IDs, OR-пулы, hide/show/remove,
Undo, F5, selection, refinement, History, reuse offer, чужие проекты, malformed
input → canary другого пользователя и полный 130-item paginated pool.

Артефакты: [labels](screenshots/search-understanding-throughput/labelled.json),
[первый holdout](screenshots/search-understanding-throughput/holdout-first.json),
[replay](screenshots/search-understanding-throughput/holdout.json),
[properties](screenshots/search-understanding-throughput/properties.json),
[sequences](screenshots/search-understanding-throughput/sequences.json),
[Edge](screenshots/search-understanding-throughput/browser.json).
Desktop/mobile PNG просмотрены как изображения. В mobile действительно открыта
панель фильтров, проверены красный/200; checkbox и F5 проверены во всех восьми случаях.

Промежуточные неуспехи сохранены: первый backend выявил пять регрессий,
первые sequences — неверный ключ harness и пропуск baseballcap в cap, позднее
проверка нового теста — отсутствующий import `select`. Всё исправлено и повторено.
Один browser-run прерван намеренным restart API; завершённые проверки не выданы
за непрерывный run, оставшиеся mobile C/D повторены после восстановления.

## Почему импорт простаивал и что изменено

`AVAILABILITY_REFRESH` оставлял неудачную карточку первой в pending. `finish`
считал любую ошибку, кроме TIME_BUDGET, отказом всего поставщика. Исторический
VTO_NOT_FOUND давал failures=5 и общий четырёхчасовой backoff, блокировавший full
scan. Refresh повторно искал старый slug, хотя реальный parent ID уже сохранён.

Дополнительно `gather` ждал обе карточки перед ingest: быстрая соседняя теряла
прогресс при timeout медленной. Страницы вариантов/AJAX внутри крупной семьи
не имели durable cursor и загружались заново после нового slice.

Исправления:

- Card-level ошибки отделены от source-wide 403/429/5xx/network. Старый
  ошибочный card cooldown исправляется только при отсутствии активной lease.
- Известный refresh использует реальный parent ID; карточка с ошибкой отходит
  в durable failed list с ограниченными попытками, не блокируя остальные.
- Устаревающий initial scan получает ограниченную очередь исполнения между
  refresh, сохраняя приоритет интерактивной работы.
- Готовый parent сохраняется независимо от медленного соседа. При отмене все
  принадлежащие slice HTTP tasks отменяются до освобождения supplier lease.
- HTML detail, страницы real API children и AJAX-ответы сохраняются в том же
  checkpoint. Resume не загружает подтверждённые страницы заново; дата evidence
  не превращается в «сейчас» при повторном использовании.
- Подтверждённые API-варианты с собственными IDs/ценами могут сохраняться до
  дополнения stock. Detail failure допускает проверяемый API core fallback.
  Неполный core не стирает прежние полные данные существующего offer.
- Дедуплицированы повторные concept upserts/counts внутри batch. Добавлены
  пооперационные HTTP/parse/pacing/DB metrics и виды запросов.
- PostgreSQL сериализует обновление одного offer; запоздавшая старая запись
  не заменяет новый ноль. Числовой UNKNOWN остаётся NULL, incoming и общий
  parent stock не превращаются в свободный остаток каждого child.

Параллелизм **2**, delay **0.3 s**, HTTP connection pooling, supplier lease,
circuit breaker, Retry-After и bounded retries сохранены. Второго crawler нет.
Слой background worker не добавляет к VTO собственные повторные retries/delay.
Изображения сохраняются как URLs; matting, full-size photo processing и LLM
не входили и не добавлены в критический путь ingest.

## Измерения

На **непустой изолированной PostgreSQL** сравнен committed `dd18c69` и новый код:
card cooldown **899.99 s → 0 s**; семь прогретых транзакций по четырём вариантам
Melango — median **28.548 → 27.552 ms**, SQL **44 → 43**.
Это маленький DB-эффект, не многократное ускорение всего обхода.
Конкурентный late refresh сохранил более новый stock=0.
[Replay](screenshots/search-understanding-throughput/import-replay.json).

Реальный единственный worker, пять ограниченных окон при неизменном source limit:

| Окно | Секунды | Завершено parents | Добавлено offers | HTTP | HTTP errors |
|---|---:|---:|---:|---:|---:|
| Первый | 95.047 | 28 | 51 | 43 | 0 |
| Второй | 95.047 | 8 | 10 | 38 | 0 |
| Resume | 95.047 | 1 | 1 | 32 | 0 |
| Core/enrichment | 95.047 | 5 | 311 | 31 | 0 |
| Диагностический | 29.281 | 10 | 175 | 15 | 0 |

Семьи различаются числом вариантов и ценой HTTP; эти окна **не являются A/B
speedup comparison**. Подтверждены устранённое ожидание, отсутствие повторной
загрузки сохранённых страниц и реальный прогресс, а не обещание постоянного
parents/hour. На recorded responses проверено: после отмены на первой странице
resume делает только следующую страницу, всего три запроса включая единственный HTML.

Во время каждого окна выполнено 10 index-first поисков. Последний smoke p95
(10 samples, фактически максимум) **2.156 s**. Runtime во время этой приёмки:
pool checkout p95 **0.0073 ms**, max **144.5 ms**; loop lag p95 **19.94 ms**,
max **440.16 ms**; active/queued/rejected = **0/0/0** в конце окна.
[Снимок runtime в конце sync](screenshots/search-understanding-throughput/runtime-during-sync.json)
сохранён отдельно от более поздних rolling-buffer counters в preservation.
Max lag не скрывается. Эти числа не характеризуют production load p95 и весь
каталог; длительная нагрузка и сеть других пользователей **NOT TESTED**.
Runtime endpoint включался только в локальном измерительном процессе с auth.

[Первое окно](screenshots/search-understanding-throughput/import-live-first.json),
[второе](screenshots/search-understanding-throughput/import-live-second.json),
[resume](screenshots/search-understanding-throughput/import-live-resume.json),
[core](screenshots/search-understanding-throughput/import-live-stages.json),
[последнее](screenshots/search-understanding-throughput/import-live.json).

## Полнота и сохранность

Приёмочный снимок: **7182 offers / 2548 families**, против **6634 / 2496** до
задачи. Offer не считается отдельной физической моделью. Каталог вырос на
**548 offers / 52 families** из подтверждённых VTO responses.
Полная coverage сверка ещё не завершена; девять исторических failed cards
остаются видимыми. Актуальные IDs, retry/stage/progress, stock counts и runtime:
[preservation.json](screenshots/search-understanding-throughput/preservation.json).

Проценты: discovery — пройденные страницы/заявленные страницы; indexing —
уникальные обработанные parents/заявленный total с оговоркой о меняющемся источнике;
enrichment/stock — собственные числители по offers. Это разные знаменатели.
Строгая завершённость требует полного совпадения parent ID sets и стабильных
totals. До этой сверки никакого «100% VTO» нет. Неполный обход не удаляет offers.

Fingerprint checks подтвердили прежние users/projects/collection items,
15 документов, 20 revisions и 3 public shares; encryption key не изменён.
Основная схема осталась на 0025. Исторические 0021/0022 теперь имеют frozen DDL,
чтобы чистая тестовая установка не подтягивала будущие ORM columns; это не reset
и не повторное развёртывание основной БД.

Для переговоров подготовлен [read-only data contract](vto-readonly-data-contract.md).
Не получены private WooCommerce credentials, прямой SQL-доступ или право считать
публичный API полным стабильным экспортом. Внешний Vercel deployment не выполнялся.

## Финальные quality gates и границы

| Проверка | Статус / фактический результат |
|---|---|
| Полный backend на окончательном коде | PASS: 618 passed, 1 opt-in deselected, 284.22 s |
| Frontend tests | PASS: 66 |
| Ruff / ESLint / TypeScript / production build / diff check | PASS |
| HTTP A–D, persisted intents, все matching IDs | PASS: 8 случаев, punctuation/context equivalence |
| Stateful HTTP на отдельной PostgreSQL | PASS: 100 sequences / 1328 HTTP |
| VTO faults на отдельной PostgreSQL | PASS: 17 |
| Реальный Edge desktop/mobile, HUD, selection, F5 | PASS: 8 случаев |
| Начальный импорт / полная ID-set сверка | PARTIAL: импорт не закончен |
| Sustained throughput, production p95, внешний deployment | NOT TESTED |

[Машинная сводка](screenshots/search-understanding-throughput/quality-gates.json).
Опт-ин тест не выдан за пройденный; широкая нагрузка на VTO не выполнялась.
Fault replay на PostgreSQL использует отдельную схему на **55433** для каждого
теста. Новая пустая БД для цепочки Alembic также находилась только в этом
одноразовом кластере. Основная PostgreSQL **55432** не сбрасывалась.

Измерения не означают, что все 9 исторических проблемных карточек исправлены,
или что источник публикует числовой остаток каждого варианта. Per-variant resume
проверен для initial full scan; refresh больших семейств по-прежнему получает
семью в рамках своего slice. Его новые ID lookup/card retry проверены fixtures
и PostgreSQL, но отдельного live refresh всех 41 исторических URL в этой приёмке
не было. Fairness ограничивает вытеснение initial scan такими refresh.

Точная очистка: удалены **2** disposable аккаунта, **64** принадлежащих им проекта,
их sessions/research/collections по связям БД; созданных в основной БД документов
не было. Закрыты изолированный API, PostgreSQL 55433 и Edge debugging listener.
Удалены только принадлежащие этому run временный кластер/профиль и ledgers с
cookies. Постоянные gold labels, tests, PDF/assets пользователя, рабочие логи,
исходная БД, encryption key, supplier jobs и checkpoints сохранены.

API перезапущен с обычными настройками из конфигурации после измерения;
временные overrides background/debug сняты. PostgreSQL, Ollama, API и frontend
остаются запущенными. [Проверка после очистки](screenshots/search-understanding-throughput/final-check.json):
API/frontend/proxy — 200; fingerprints исходных данных снова совпали; временные
порты 55433/9226 закрыты. Offers уже **7183**: штатный background worker продолжил
обход. Это следующий снимок, не исправление приёмочного числа 7182.

Воспроизведение: `scripts/check_search_safety.py regression --stage ...` расширяет
существующий harness. `prepare` требует свободного порта основного API и начинает
с записи baseline; далее `before`, `admission-before`, изолированный setup,
`populate-isolated`, `labelled`, `holdout`, `properties`, `sequences`, `after`,
`audit-controls`, `admission-after`, `vto-baseline`, `vto-replay`, `migration-chain`,
ограниченный `vto-live`, `browser`, `verify`, `restore-defaults`, `cleanup`.
Не повторять `prepare` поверх существующего ledger. Для PostgreSQL fault replay
есть opt-in `VTO_FAULT_POSTGRES_URL` только на проверенный одноразовый 55433.
