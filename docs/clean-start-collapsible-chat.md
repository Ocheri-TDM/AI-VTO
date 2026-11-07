# Чистый старт, сворачиваемый чат и прокрутка

7 ноября 2026. **PASS** для описанного ниже локального frontend-сценария.
Проверен production build на установленном Microsoft Edge; Chromium runtime
и новые зависимости не устанавливались.

## Правила отображения

Новый пустой чат показывает приветствие и один центральный composer. Правой
панели, её разделителя, кнопки открытия и блока выбранных с нулём нет.
Закрытый desktop track имеет ширину 0; основная область занимает всё место
справа от списка чатов. Набор текста ничего не открывает.

Первый валидный submit сразу открывает переписку с исходным текстом и статусом
неподтверждённой отправки. Ответ сервера заменяет локальную проекцию по
request_id. Ошибка сохраняет черновик и явно сообщает, что отправка не
подтверждена. Пустая строка и ошибка длины до отправки панель не открывают.

«Свернуть чат» освобождает колонку и возвращает фокус на компактную кнопку
«Чат». Анимация — 200 ms, закрываемая панель сразу inert и удаляется после
перехода. Reduced motion отключает переход. На mobile используется прежний
Sheet; сетка под ним не сжимается. Глобальный обработчик Escape не добавлялся.

Видимость отделена от hooks запроса, истории, research и коллекции. Закрытие
не отменяет операцию, не очищает черновик и не запускает поиск заново.
Предпочтение открытия хранится по пользователю и чату. При автоматическом
создании первого проекта переносится последнее ручное решение: запоздалое
создание чата или ответ поиска не открывают закрытую панель.

Наличие переписки определяется сообщениями, research, сохранённым признаком
истории и отправкой, независимо от количества товаров. Завершённая загрузка
истории, research и коллекции учитывается отдельно. При переключении research
и сообщения предыдущего чата не показываются. Неизвестный счётчик коллекции
отображается как загрузка. Ошибка чтения research оставляет восстановление
с возможностью повтора, а не притворяется пустым новым чатом.

После поиска сохранён прежний компактный HUD с подборкой справа от фильтров,
включая счётчик 0. Если research отсутствует, но в проекте есть выбранные,
доступна небольшая кнопка со счётчиком. Выбор и переход к предложению используют
прежний ProjectCollection API.

## Причины и компоненты

| Файл | Изменение |
|---|---|
| `research-workspace.tsx` | Условия старта/восстановления, одна форма, локальная отправка, desktop/mobile панель, компактный доступ к сохранённой коллекции |
| `workspace-panels.ts`, `use-chat-panel.ts` | Семантика состояний и отдельное управление видимостью/фокусом; перенос решения при создании чата |
| `use-conversation.ts`, `use-research.ts` | Готовность чтения и привязка данных к текущему чату |
| `conversation.tsx` | Действие сворачивания, статусы отправки, нативная клавиатурная прокрутка истории |
| `research-hud.tsx` | Загрузка коллекции вместо вымышленного нуля |
| `app-navbar.tsx`, `research.css` | Сам title DOM, prop context и его стили удалены; нет пустой замены |
| `research.css` | Колонка 0/300–380 px, переход, ограничение mobile body, исправление лишней прокрутки короткого списка чатов |
| `scrollbars.css`, `layout.tsx` | Общие semantic tokens и оформление реальных scroll-контейнеров |

Исходный workspace всегда монтировал правый aside и при отсутствии research
показывал большой счётчик через `?? 0`. Данные research сохранялись в hook
до эффекта смены чата. У списка чатов старый `min-height:100%` складывался
с высотой отдельного заголовка: даже короткий список прокручивался на 48 px.
Эти причины устранены без изменения API.

Navbar сохраняет бренд, проекты, предложения, выбор темы, пользователя и выход.
`header-project-title`, `app-navbar-context`, `research-top-project` отсутствуют
в исходном UI и проверенном DOM. Название в боковом списке и автопереименование
сохраняются; title документа предложения не менялся. Высота desktop navbar
в измеренном сценарии осталась 64 px.

Прокрутка остаётся нативной. Стандартные свойства назначены самим контейнерам;
в Edge WebKit-ветка сбрасывает конкурирующий standard override в auto и задаёт
10 px, закруглённый ползунок и theme tokens. Проверенные цвета ползунка/дорожки:
тёмная `#737f76 / #181e1a`, Pulse `#7c8b80 / #edf1ec`.
Forced colors возвращает системные свойства. Для coarse pointer явный размер
сбрасывается. Нет JS-scroll engine, глобального smooth scroll или скрытия полос.

## Реальная проверка

Основной API и заполненная PostgreSQL использованы только через отдельные
тестовые аккаунты/проекты. Реальные запросы: «Рюкзак Melango» с четырьмя
вариантами и «ручки до 1 тенге» с нулевым результатом. Выбор/F5/переключения
проверены на сохранённых данных. Задержки HTTP-ответов, сетевая ошибка и
история из 100 сообщений — явно отделённые UI-фикстуры. Искусственная история
не записывалась в БД. Это проверка интерфейса, не новый аудит качества поиска.

| Проверка | Результат | Статус |
|---|---|---|
| Frontend state/component suite | 79 passed, включая 7 новых регрессий | PASS |
| Основной Edge-сценарий | 30 проверок: старт, ввод, submit, close/reopen, late response, F5, смена чата, slow hydration, отсутствие результата, ошибка, navbar | PASS |
| Native controls | 13 проверок: validation, GET/POST/subscriptions, dropdown, selected/model variants и Escape в обеих темах | PASS |
| Первый submit без проекта | 8 проверок в двух темах: немедленная панель, закрытие во время создания, ответ в закрытую панель, восстановление истории | PASS |
| Desktop / mobile | 1440×1000 и 390×844; DOM и реальные screenshots | PASS |
| Эквивалентные zoom viewport/DPR | 1152×800 / 1.25 и 960×667 / 1.5 без горизонтального overflow | PASS |
| Прокрутка | Реальные 10 px и разные theme tokens; длинная история/выбор/варианты; keyboard и wheel; без дополнительного body scroll desktop | PASS |
| Accessibility media | Emulated reduced motion и forced colors | PASS |
| ESLint / TypeScript / production build | Успешно после финального исправления | PASS |
| Ruff acceptance scripts / git diff --check | Успешно | PASS |
| Backend suite | В этой frontend-итерации не запускался; backend source не менялся | NOT TESTED |
| Firefox/Safari, физический touchpad/touch, screen reader, нативное меню zoom | Не проверялись; mobile/zoom использовали Edge emulation | NOT TESTED |

Три закрытия/открытия не добавили сетевых операций: GET **10 → 10**,
POST **0 → 0**, EventSource created/live **0/0 → 0/0**. Этот index-first поиск
завершился без активного SSE; длительная живая SSE-сессия отдельно не заявляется.
Первая локальная проекция зафиксирована до удерживаемого HTTP ACK за 0.009 s;
это единичное наблюдение UI, не latency/p95 поиска.

Промежуточный fresh-account тест обнаружил реальное повторное открытие панели
при создании первого чата; исправление подтверждено финальным повтором в двух
темах и постоянным unit-тестом. Ранние запуски acceptance также выявили ошибки
самой проверки: преждевременное ожидание создания чата, пустой IN при read-only
сверке, незавершённый синтетический keyboard gesture и отсутствие instrumentation
в новой CDP-сессии. Harness исправлен; эти запуски не посчитаны успешными.

Доказательства: [30 основных проверок](screenshots/clean-start/browser.json),
[13 controls](screenshots/clean-start/controls.json),
[8 fresh-account](screenshots/clean-start/fresh.json).

## Скриншоты

| Состояние | Тёмная тема | Pulse |
|---|---|---|
| Новый пустой экран | [PNG](screenshots/clean-start/current-empty.png) | [PNG](screenshots/clean-start/pulse-empty.png) |
| Открытая переписка | [PNG](screenshots/clean-start/current-open.png) | [PNG](screenshots/clean-start/pulse-open.png) |
| Та же выдача, чат свёрнут | [PNG](screenshots/clean-start/current-collapsed.png) | [PNG](screenshots/clean-start/pulse-collapsed.png) |
| Длинная история, UI fixture | [PNG](screenshots/clean-start/current-long-history.png) | [PNG](screenshots/clean-start/pulse-long-history.png) |
| Выбранные | [PNG](screenshots/clean-start/controls-current-selected.png) | [PNG](screenshots/clean-start/controls-pulse-selected.png) |
| Варианты модели | [PNG](screenshots/clean-start/controls-current-variants.png) | [PNG](screenshots/clean-start/controls-pulse-variants.png) |

Мобильные: [открытый чат](screenshots/clean-start/mobile-chat.png),
[выдача после закрытия](screenshots/clean-start/mobile-grid.png).
PNG просмотрены визуально; API 200 сам по себе не считался проверкой layout.

## Сохранность и воспроизведение

[Финальная сверка и cleanup](screenshots/clean-start/cleanup.json): исходные
3 пользователя, 4 проекта, 60 сообщений, 17 выбранных позиций, 16 документов,
20 ревизий и 3 share-записи сохранили row fingerprints. Сохранены 10 760 исходных
offer identities, 259 файлов документов и encryption key. Hashes 152 Python
файлов backend/миграций совпали с baseline. Это измерение сохранности, не
заявление о завершении импорта VTO или неизменности данных при его штатном sync.

Миграций, изменений parser/importer/stock/link/auth или перезапуска API не было.
Перезапущен только frontend с финальным build. По точным ID удалены 3 тестовых
аккаунта и 5 проектов; документы не создавались. Ledger с cookies, отдельный
профиль Edge и listener 9226 удалены/закрыты. API, frontend и health proxy после
очистки вернули 200. Основные сервисы работают.

Постоянные тесты: `workspace-panels.test.cjs`, проверка DOM navbar в
`nav-delete.test.cjs`. Прежний `chat_workflow_browser.py` теперь проверяет
название в боковом списке, а не в удалённом navbar, и использует кнопку «Чат».
Acceptance: `scripts/check_clean_start.py prepare` → `web` после build →
`browser` → `controls` → `fresh` → `verify` → `cleanup`.
Cleanup ограничен записанными ID и отдельным browser profile. Повторный `prepare`
с существующим ledger запрещён.

## Уточнение оформления Pulse — 7 ноября 2026

После замечания пользователя визуальное различие усилено: прежний ползунок
Pulse `#7c8b80` был почти таким же серо-зелёным, как в тёмной теме. Теперь
дорожка использует `--ui-surface-page` (`#f3f5f2`), нейтральный ползунок —
`#7b838b`, hover — `--ui-brand` (`#f42902`), active/перетаскивание —
`--ui-accent-active` (`#cf2608`). Тёмная тема сохраняет прежние цвета.
Forced-colors сбрасывает также новое active-состояние в системное оформление.

**PASS:** 79 frontend-тестов, ESLint, TypeScript, production build, Ruff и
diff check; 14 отдельных проверок установленного Edge. Цвета подтверждены
по пикселям настоящего scrollbar при наведении и нажатии, проверены drag,
восстановление темы после F5 и forced-colors. Для этого использовалась явно
подписанная anonymous UI fixture с production CSS четырёх scroll-контейнеров;
пользовательские данные и БД не изменялись. Ранний drag-check был исправлен:
нужно дождаться compositor после синтетического жеста; финальный повтор прошёл.

[Проверки](screenshots/scrollbar-pulse/checks.json) ·
[Pulse обычно](screenshots/scrollbar-pulse/pulse-normal.png) ·
[Pulse hover](screenshots/scrollbar-pulse/pulse-hover.png) ·
[Pulse active](screenshots/scrollbar-pulse/pulse-active.png).

`scripts/check_scrollbar_themes.py` сохранён как браузерная регрессия. Отдельный
профиль Edge удалён, listener 9226 закрыт. Перезапущен только frontend; API,
frontend и proxy отвечают 200. Другие браузеры в этом уточнении — NOT TESTED.
