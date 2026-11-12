# Компактный интерфейс предложения и кнопки Pulse

12 ноября 2026. Локальная production-сборка обновлена и проверена в установленном
Edge. Новый frontend на Vercel **NOT DEPLOYED**: вход и deployment не выполнялись.

## Изменения

**PASS.** Из панели «Выбор клиента» удалены история действий и пояснение о
браузерных сессиях. Сохранены отдельные посетители, текущие отметки, счётчики,
фотографии, SKU, версия предложения и время изменения. Отдельного запроса истории
не было: текущий выбор и события приходят одним feedback DTO. Этот запрос,
пагинация посетителей, SSE/reconnect, sequence IDs, идемпотентность и уведомления
сохранены. Серверный контракт и данные событий не менялись.

**PASS.** Убраны подпись «Клиентская ссылка», поле URL и его стили. Остались
обновление публикации, открытие клиентского вида, копирование, статус и дедлайн.
Общий `copyOfferLink` используется в редакторе и списке. Перед копированием
заново читается действующая ссылка; локальные, HTTP, editor/API-адреса отклоняются.
Успех появляется после разрешения Clipboard Promise. При отказе — короткая ошибка
и повтор тем же действием, без поля, prompt или alert. Одновременное копирование
в редакторе блокируется, подпись кнопки не меняет ширину.

**PASS.** Новые semantic tokens основных кнопок Pulse:

| Состояние | Вычисленный фон | Текст/иконка | Контраст |
|---|---|---|---:|
| Normal / focus | `#D62402` | `#FFFFFF` | 5.106:1 |
| Hover | `#C92302` | `#FFFFFF` | 5.638:1 |
| Pressed | `#BA2002` | `#FFFFFF` | 6.373:1 |
| Основная кнопка Current | `#CCED91` | `#111413` | 14.197:1 |

`--ui-primary-bg/fg/hover/pressed` связаны с прежними button tokens; брендовый
`#F42902` сохранён для других акцентов. Коммерческая страница по прежнему контракту
имеет собственную светлую Pulse-поверхность независимо от темы сайта. Основные
кнопки общего интерфейса Current остались зелёными. На зелёных поверхностях
сохраняется тёмный текст; secondary и destructive не получили primary-стиль.

Geist, 15 px, weight 600, нормальный интервал, min-height 44 px. Длинные подписи
переносятся. Нет stroke, text-shadow или синтетической жирности. Устранены общий
hover brightness и переход между цветами текста/фона: промежуточные смешанные
цвета не выдаются за проверенные tokens. Busy-кнопки сохраняют читаемость;
у отправки зарезервирована ширина длинной busy-подписи. Disabled остаётся отдельным
состоянием. Вычисленный контраст focus к соседней поверхности прошёл порог 3:1.
Это проверка перечисленных controls, не полный аудит доступности сайта.

## Logout: воспроизведение и исправление

До исправления обычный `api.logout()` отправлял пустое тело с
`Content-Type: application/json`. Действующий JSON guard возвращал **400
INVALID_JSON**, а AuthGate уже убирал приватный экран. Сессия на сервере оставалась
действующей — подтверждено последующим HTTP 200 на `/api/auth/me`.

Исправлен только запрос frontend: POST logout передаёт `{}`. Серверная
авторизация, Origin, cookie policy и JSON guard не ослаблялись. **PASS** в новом
локальном build: кнопка выхода закрыла SSE и приватный UI; me, notifications и
document GET вернули **401/401/401**. Через две секунды новых SSE не появилось.
Вход второго реального тестового аккаунта в той же вкладке показал только его
предложение и пустой центр уведомлений. Добавлена постоянная wire-регрессия logout.
Старая Vercel-сборка этот frontend fix ещё не получила.

## Проверки

| Проверка | Результат | Статус |
|---|---|---|
| Полный frontend suite | 94 passed | PASS |
| Backend selection/notification regressions | 21 passed, 39.42 s | PASS |
| ESLint / TypeScript / production build | Успешно | PASS |
| Ruff двух acceptance scripts / git diff --check | Успешно; только предупреждения Git LF/CRLF | PASS |
| Edge, новый локальный production UI | 95 проверок | PASS |
| Реальный выбор / снятие / разные посетители / F5 / reconnect / колокольчик | Основной HTTP API и заполненная PostgreSQL, без подмены SSE | PASS |
| Копирование: native Clipboard, отказ, pending Promise, повтор | Native локально; отказ/pending — изолированные spies | PASS |
| Реальный HTTPS Clipboard существующего Vercel-интерфейса | Native write/read только своего тестового URL; сравнение без вывода URL | PASS |
| Повтор копирования | Те же document, draft, share, expiry и revisions | PASS |
| Шесть основных действий Pulse | Все четыре enabled состояния, цвет иконки, opacity/filter, focus | PASS |
| Current | Зелёные primary, тёмный текст, normal/hover/pressed/focus | PASS |
| Mobile 390 px, длинная кнопка публикации | Без horizontal overflow и обрезания | PASS |
| Настоящий Edge zoom 125% / 150%, обе темы, editor/workspace | В отдельном профиле: settings 1.25/1.5, DPR 1.25/1.5, innerWidth 1152/960 | PASS |
| Logout → приватные GET → другой аккаунт; новый чат → первый поиск | Фактические HTTP и DOM | PASS |
| Новые косметические изменения на публичном frontend | Внешний редактор всё ещё показывает старое URL-поле | NOT DEPLOYED |
| Полный backend suite / все браузеры / screen reader / нагрузка | В этой UI-итерации не выполнялись | NOT TESTED |

Первый frontend-прогон: 92/93, тест заметил скрытую вторую busy-подпись в DOM.
Резерв ширины перенесён в CSS, текст DOM снова соответствует действию. Ранние
browser-прогоны выявили hover brightness, смешение переходных цветов и настоящий
400 Logout; исправлены до финального прогона. Две ошибки acceptance исправлены:
ожидание загрузки visitor state перед кликом и сравнение focus с ближайшим
непрозрачным фоном вместо фона body. Эти промежуточные запуски не названы PASS.

Доказательства: [95 browser checks](screenshots/offer-ui/after.json),
[вычисленные цвета](screenshots/offer-ui/button-colors.json),
[настоящий масштаб](screenshots/offer-ui/native-zoom.json).

| Экран | До | После |
|---|---|---|
| Выбор клиента | [PNG](screenshots/offer-ui/before-selection.png) | [PNG](screenshots/offer-ui/after-selection.png) |
| Публикация | [PNG](screenshots/offer-ui/before-publication.png) | [PNG](screenshots/offer-ui/after-publication.png) |
| Pulse primary | [PNG](screenshots/offer-ui/before-buttons.png) | [PNG](screenshots/offer-ui/buttons-pulse.png) |

[Mobile](screenshots/offer-ui/publication-390.png),
[150%](screenshots/offer-ui/zoom-1.5-pulse-editor.png),
[Current](screenshots/offer-ui/buttons-current.png),
[ошибка копирования](screenshots/offer-ui/copy-error.png).
Итоговые publication, selection, Pulse/Current, mobile и zoom PNG визуально
просмотрены. Capability скрыта на исходном screenshot поля; в отчёте и evidence
нет share key. Личный буфер до тестовой записи не читался. Chromium runtime
не устанавливался; использован отдельный профиль установленного Edge.

## Сохранность и воспроизведение

**PASS:** совпали fingerprints исходных 3 пользователей, 4 проектов, 66 сообщений,
26 collection items, 17 документов, 22 ревизий и 4 shares. Сохранены hashes
309 исходных файлов и encryption key. Все 157 backend/migration Python-файлов
совпали с baseline: миграций и изменений backend нет. Сохранены 24 101 исходная
offer identity; штатный импорт продолжался и на cleanup было 24 608 offers.
Это не заявление о неизменности всех коммерческих данных при работающем импорте.
Исторические PDF/XLSX не перегенерировались. VTO, stock, scheduler и checkpoints
кодом задачи не менялись.

[Exact-ID cleanup](screenshots/offer-ui/cleanup.json) удалил только два аккаунта,
шесть проектов и три документа текущего run с их зависимыми тестовыми данными
и document directories. Исходные events/visitors/notifications не очищались;
после удаления собственных тестовых ресурсов принадлежащих им строк осталось 0.
Secret ledger и временный профиль Edge удалены; debugging listener закрыт.
API, frontend и Vercel API proxy после cleanup: HTTP 200. Основные сервисы работают.

`scripts/check_offer_ui.py`: `prepare` → `setup` → `before` → build → `web` →
`after` → `verify` → `cleanup`. Новый `after` рассчитан на один финальный прогон:
его Logout отзывает исходную тестовую session. Acceptance использует прежний
guarded harness, отдельный `.local/offer-ui-ledger.json` и точные disposable IDs.
Для повторной приёмки после cleanup нужен новый prepare. Новый frontend deployment
выполняется отдельно; текущий production build уже запущен локально.
