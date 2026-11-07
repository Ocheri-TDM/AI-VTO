# Управление чатом справа

7 ноября 2026. **PASS** для локальной frontend-приёмки в установленном Edge.

## До и после

Раньше после закрытия панели кнопка «Чат» вставлялась внутрь прокручиваемой
основной области слева, отдельной строкой перед «Историей». Она увеличивала
вертикальный отступ и исчезала при прокрутке.

Теперь существующая строка действий с «Историей» находится вне скролл-контейнера
выдачи. Кнопка с контурной `MessageSquare` и постоянной подписью «Чат» стоит
справа. Её появление не меняет высоту строки. Отдельная боковая колонка, overlay
с большим z-index и дополнительный флаг видимости не добавлены.

Фактические desktop-координаты при ширине 1440 px: верх **76 px** при нижней
границе navbar **64 px**, правый край **1420 px**, высота **44 px**, иконка
**20 px**. Кнопка сворачивания имеет ту же высоту и координату Y, контурную
`PanelRightClose`, подпись «Свернуть» и доступное имя «Свернуть чат».
«История» сохраняет Y=76 при открытии/закрытии. Свернутая центральная область
доходит до правого края 1440 px; третья grid-колонка остаётся шириной 0.

Используются существующие spacing/radius/color tokens и установленный Lucide.
В Pulse иконка использует `--ui-brand`, в текущей теме — `--accent`.
Новых библиотек, hover-раскрытия или пульсации нет. Переход 200 ms и reduced motion
сохраняются. Navbar title не возвращён, на странице предложений кнопки нет.

## Компоненты

| Компонент | Изменение |
|---|---|
| `research/chat-panel-toggle.tsx` | Общая настоящая кнопка открытия/сворачивания, иконка, видимый текст, tooltip, aria-expanded/controls и mobile aria-haspopup |
| `research/research-workspace.tsx` | Закреплённая строка действий, удаление старой левой и дублирующей нижней mobile-кнопки, прежний Sheet |
| `research/conversation.tsx` | Общая кнопка в заголовке панели; возможность не фокусировать textarea на мобильном |
| `hooks/use-chat-panel.ts` | Прежнее состояние/предпочтение; desktop focus в composer, mobile focus на сворачивании, возврат на открытие с preventScroll |
| `common/sheet.tsx` | Необязательные id и closeControl для chat drawer; остальные панели сохраняют прежнее закрытие |
| `app/research.css` | Строка вне прокрутки, согласованные размеры, safe-area, flex/grid min-height; mobile выдача прокручивается внутри workspace |

На мобильном navbar занимает свою фактическую высоту, далее строка действий
и один скролл-контейнер результатов. Кнопка остаётся справа, не уменьшая ширину
сетки. Открывается тот же native dialog `#workspace-chat`; фокус получает
«Свернуть», а не textarea. Это не инициирует экранную клавиатуру при повторном
открытии. Закрытая панель удаляется из Tab-порядка; modal выбранных товаров
находится в native top layer выше строки действий.

## Проверка

| Проверка | Результат | Статус |
|---|---|---|
| Frontend component/state suite | 83 passed; 4 новых теста для текста/иконок, ARIA, mobile focus и opt-in Sheet | PASS |
| Предыдущая функциональная цепочка Edge | 30: пустой чат, submit, close/reopen, поздний ответ, F5, hydration, выбор, нулевая выдача/ошибка, navbar | PASS |
| Специальные проверки размещения | 29: координаты, одинаковая высота, прокрутка, modal, draft/history/selection, Network, проекты и offers | PASS |
| Первый запрос до создания проекта | 8 проверок в двух темах; закрытие не отменяется созданием чата/ответом | PASS |
| Desktop и mobile | Обе темы; 1440, 390 и 320 px; screenshots и DOM | PASS |
| Эквивалентный масштаб | 1152×800 / DPR 1.25, 960×667 / DPR 1.5, обе темы без overflow | PASS |
| Reduced motion / focus | Отключён переход, возврат на кнопку, mobile textarea не автофокусируется | PASS |
| ESLint / TypeScript / production build / diff check | После финальных UI-изменений | PASS |
| Ruff acceptance scripts / прежний browser selector | Успешно | PASS |
| Реальная OS-клавиатура, нативное меню zoom, другие браузеры, screen reader | Не проверялись; использован Edge emulation | NOT TESTED |
| Backend suite | В этой локальной UI-итерации не запускался; backend source не менялся | NOT TESTED |

Реальный запрос «ручки» дал длинную выдачу с первой страницей 100 карточек.
Кнопка оставалась на Y=76 после нескольких экранов прокрутки. Парные desktop
скриншоты сделаны при `main.scrollTop=1400` в обоих состояниях; размеры карточек
естественно меняются при расширении сетки.

Во время трёх дополнительных циклов POST остался **1 → 1**, EventSource
created/live — **0/0 → 0/0**, GET — **12 → 13**. Циклов повторной загрузки нет.
Этот index-first запрос завершился без долгой активной SSE; отдельно такая
SSE-сессия не заявляется. Ранее сохранённая коллекция, четыре подтверждённых
сообщения и неотправленный черновик сохранились после F5 и смены проекта.

Доказательства: [30 функциональных проверок](screenshots/right-chat/browser.json),
[29 проверок размещения](screenshots/right-chat/placement.json),
[8 fresh-account проверок](screenshots/right-chat/fresh.json).

## Скриншоты

Прежнее размещение: [слева перед историей](screenshots/clean-start/current-collapsed.png).

| Состояние | Текущая тема | Pulse |
|---|---|---|
| Выдача прокручена, чат закрыт | [PNG](screenshots/right-chat/right-current-collapsed.png) | [PNG](screenshots/right-chat/right-pulse-collapsed.png) |
| Та же позиция прокрутки, чат открыт | [PNG](screenshots/right-chat/right-current-open.png) | [PNG](screenshots/right-chat/right-pulse-open.png) |
| Mobile, чат закрыт | [PNG](screenshots/right-chat/right-current-mobile-collapsed.png) | [PNG](screenshots/right-chat/right-pulse-mobile-collapsed.png) |
| Mobile, чат открыт | [PNG](screenshots/right-chat/right-current-mobile-open.png) | [PNG](screenshots/right-chat/right-pulse-mobile-open.png) |

[Модальная подборка поверх кнопки](screenshots/right-chat/right-modal.png).
Контрольные desktop/mobile PNG визуально просмотрены.

## Сохранность и повтор

Backend, схема, importer, stock policy, ссылки и предложения не изменялись.
[Сверка и точечная очистка](screenshots/right-chat/cleanup.json) подтверждают
неизменность исходных 3 пользователей, 4 проектов, 62 сообщений, 17 выбранных
позиций, 16 документов, 20 ревизий и 3 share-записей. Сохранены 11 177 исходных
offer identities, 259 файлов и encryption key; hashes 152 backend/миграционных
файлов совпали. Это snapshot сохранности, не заявление о полноте VTO.

Удалены только записанные в ledger 3 disposable аккаунта и 5 проектов. PDF и
документы не создавались. Отдельный профиль Edge и cookies/ledger удалены,
listener 9226 закрыт. Перезапущен только frontend. API, frontend и health proxy
после очистки вернули 200; основные сервисы работают.

Постоянные регрессии — `apps/web/tests/chat-panel-toggle.test.cjs` и
`scripts/check_right_chat_browser.py`. Прежний mobile acceptance selector теперь
открывает правую кнопку вместо удалённой нижней. Воспроизведение:
`scripts/check_right_chat.py prepare` → frontend build → `web` → `browser` →
`placement` → `fresh` → `verify` → `cleanup`. Ledger `.local/right-chat-ledger.json`
содержит cookies и не публикуется; cleanup проверяет точные ID и отдельный profile.
