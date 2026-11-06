# Geist Sans в интерфейсе Souvenir Studio

Проверено 5 ноября 2026 года. Geist Sans стал основным UI-шрифтом Current и
Pulse, включая формы, раскрытые списки, окна деталей, drawer и редактор
презентаций. Русские и контрольные казахские символы действительно рисует Geist.
**Символ ₸ рисует Segoe UI:** его нет ни в полученной Google-поставке, ни в
проверенной полной официальной поставке. Полное покрытие валюты самим Geist
не заявляется.

## Подключение и изменённые файлы

До изменения UI использовал внешний CSS Manrope и системный fallback. Geist,
Medeo и существующего `next/font`-подключения не было. Проверены package.json,
lockfile, root layout, CSS обеих тем, dialog/popover и назначения font-family.
Установленные Next 16.3.4 и React 19.2.8 сохранены. Декларации установленного
Next подтверждают variable weight и subsets latin/cyrillic/cyrillic-ext;
изучена документация из `node_modules/next/dist/docs`.

| Файл | Изменение |
|---|---|
| `apps/web/src/app/layout.tsx` | Одно подключение `Geist` через `next/font/google`, CSS variable на html |
| `apps/web/src/app/globals.css` | Основной token `--font-ui`, удалён внешний Manrope `@import` |
| `apps/web/src/app/typography.css` | Общие веса, нормальный tracking заголовков, табличные цифры |
| `scripts/check_site_theme.py` | Изолированный режим `--font-ui`, сетевые наблюдения, cleanup |
| `scripts/check_geist_ui.py` | Постоянная приёмка в установленном Edge, actual fonts и UI |
| `scripts/check_geist_assets.py` | Проверка реальных WOFF2, cmap, весовой оси и tnum |
| `docs/screenshots/geist-ui/` | Сравнения, данные измерений и receipts |

Конфигурация: `weight: "variable"`, `style: "normal"`, `display: "swap"`,
`subsets: ["latin", "cyrillic", "cyrillic-ext"]`,
`fallback: ["Segoe UI", "Arial", "sans-serif"]`.
Цепочка: `html.--font-geist-sans → --font-ui → body → controls/dialog/popover`.
Существующие lang=ru, data-site-theme, theme-init и структура layout сохранены.
Отдельные npm-пакеты шрифтов, повторный @font-face, Geist Mono/Pixel и глобальная
установка шрифта в Windows не добавлены. Специальные логотипные/моноширинные
назначения не заменялись универсальным `* {font-family:…}`.

Основной текст, чат и поля используют 400; кнопки, labels и tabs — 500;
товарные подписи, цены в сетке и основные действия — 600; главные заголовки —
600. Variable font содержит настоящую ось 100–900. Размер текста и общая
геометрия кнопок не уменьшались; отрицательный tracking активных заголовков
сброшен в normal. Начертание не имитируется обводкой или text-shadow.

## Источник, версия и проверка файлов

Источники: [Vercel Font](https://vercel.com/font),
[официальный репозиторий Geist](https://github.com/vercel/geist-font),
[Next font documentation](https://nextjs.org/docs/app/getting-started/fonts).
Next получает файлы на стадии сборки и раздаёт их с origin приложения;
полностью автономная или зафиксированная по версии offline-сборка не заявляется.

Фактические Google WOFF2 из build имеют **Version 1.800**, family Geist.
Их SHA-256, copyright, license URL, cmap и ось весов сохранены в
[font-assets.json](screenshots/geist-ui/font-assets.json).
Формат проверен по сигнатуре `wOF2` и разбором FontTools 4.68.0/Brotli 1.2.1.
Инструменты были установлены во временный изолированный pip target; зависимости
приложения не изменялись.

Из-за отсутствия ₸ отдельно изучен полный официальный
[release v1.9.1](https://github.com/vercel/geist-font/releases/tag/v1.9.1).
Архив `geist-font-v1.9.1.zip`: 8 823 639 bytes,
SHA-256 `f1194047382839bb3ad76a82b43694083cbb56201b2791d420282376fd592506`.
В проверенных Sans Regular TTF/OTF/WOFF2 и variable normal/italic — Version 1.900,
казахские символы присутствуют, U+20B8 отсутствует. Полная поставка поэтому не
решает отсутствие тенге. Она использовалась только для аудита; приложения
сборки/установки из архива не запускались. Лицензия в архиве — `geist-font/OFL.txt`.
[Данные полной поставки](screenshots/geist-ui/official-full-font-audit.json).

Microsoft Defender, сигнатура 1.463.134.0, проверил папку полученных font assets
и официальный архив через `MpCmdRun -Scan -ScanType 3 -File … -DisableRemediation`.
Оба завершились exit 0, угроз не обнаружено. Это результат конкретных проверок,
а не гарантия полной безопасности файлов.

## Реальные символы и цифры

В установленном Edge использован CDP `CSS.getPlatformFontsForNode`, а не только
computed font-family. Итог содержит **123 записи**, включая 80 контрольных строк
в двух темах при 400/500/600/700, интерфейс и сценарии fallback.

| Проверка | Фактический результат | Статус |
|---|---|---|
| Русский алфавит и контрольные строки Tippo/Melango/подборки/презентации | cmap присутствует; browser actual font Geist | PASS |
| Ә Ғ Қ Ң Ө Ұ Ү Һ І / ә ғ қ ң ө ұ ү һ і | Все 18 символов в cmap и Geist в actual rendering, 4 веса, обе темы | PASS |
| №, тире, средняя точка, латиница и цифры | Geist в контрольных строках | PASS |
| `2 084 ₸ · 16 928 ₸ · 1 000 000 ₸` | Числа и текст Geist; ровно символы ₸ используют системный fallback | PASS с ограничением |
| Тенге в собственной гарнитуре Geist | Отсутствует в обеих проверенных поставках | FAIL: отсутствующий glyph |
| Tabular nums | tnum в бинарном font; ширины всех цифр 0–9 совпадают внутри каждого веса | PASS |

При font-size 32 px реальная ширина каждой цифры: 400 — 19.203125 px,
500 — 19.59375 px, 600 — 19.96875 px, 700 — 20.359375 px. Равенство проверяется
внутри веса, а не между весами. В реальных ценах/счётчиках применяется
`font-variant-numeric: tabular-nums`; коммерческие значения и формат не менялись.

Actual families после подключения: Geist, Segoe UI и Segoe UI Semibold.
На 500/600 тенге использует Segoe UI Semibold, на 400/700 — соответствующее
начертание Segoe UI. Визуально проверены строки и цены без квадратов,
перекрытий или нарушения базовой линии. Arial остаётся следующим fallback
в CSS, но его использование в этом Windows-прогоне не наблюдалось.

## Загрузка и fault-сценарии

| Подмножество build | Bytes | Предзагрузка |
|---|---:|---|
| Latin | 29 288 | Да |
| Cyrillic | 14 900 | Да |
| Cyrillic-ext | 7 252 | Да |
| Latin-ext | 16 540 | По требованию |
| Vietnamese | 7 968 | Нет; в обычном UI не запрашивался |
| Всего build | **75 948** | 3 preloads / **51 440 bytes** |

В обычном сценарии браузер запросил 4 уникальных файла / **67 980 bytes**.
Latin-ext запрашивается по опубликованному unicode-range, хотя ₸ в фактическом
cmap отсутствует. Его принудительная предзагрузка не добавлена.
На трёх первоначальных загрузках страниц: 4 сетевых font response и 8 cache hits.
Перезагрузка на mobile и F5 использовали по 4 cache hits. Внешних runtime-запросов
к Google Fonts/CDN — **0**. В штатной загрузке все font responses — HTTP 200,
ошибок декодирования и hydration/runtime errors нет.

Задержка удерживала реальные WOFF2-запросы через CDP Fetch до наблюдения
системного шрифта; текст был виден, после освобождения запросов actual font
снова Geist. При полной блокировке WOFF2 интерфейс оставался читаемым в Segoe UI;
после разблокировки Geist восстановился. Намеренно заблокированные запросы
отделены от штатных ошибок. Они включают попытки браузера найти другое
подмножество, не являются обычным повторным скачиванием.

Production проверен через `next start :3000`, dev — через временный
`next dev :3016` и отдельный Edge profile. В dev получены 3 font response 200,
actual Geist-SemiBold в заголовке, runtime/hydration errors отсутствуют.
[Dev receipt](screenshots/geist-ui/dev-acceptance.json).

## Сравнения UI

Одинаковые экраны до/после получены через настоящий HTTP API и UI в Edge с
изолированной SQLite: 53 fixture offers, три выбранных реальных варианта Melango
с проверенными cached source images. Основная PostgreSQL не использовалась
для тестовых записей. Просмотрены сравнения сетки, списков, вариантов, выбранных,
редактора и палитры обеих тем, мобильные экраны и контрольные символы.

| Экран | Current до / после | Pulse до / после |
|---|---|---|
| Сетка, фильтры и длинный проект | [до](screenshots/geist-ui/before/current-grid.png) / [после](screenshots/geist-ui/after/current-grid.png) | [до](screenshots/geist-ui/before/pulse-grid.png) / [после](screenshots/geist-ui/after/pulse-grid.png) |
| Раскрытый материал | [до](screenshots/geist-ui/before/current-dropdown.png) / [после](screenshots/geist-ui/after/current-dropdown.png) | [до](screenshots/geist-ui/before/pulse-dropdown.png) / [после](screenshots/geist-ui/after/pulse-dropdown.png) |
| Варианты модели | [до](screenshots/geist-ui/before/current-variants.png) / [после](screenshots/geist-ui/after/current-variants.png) | [до](screenshots/geist-ui/before/pulse-variants.png) / [после](screenshots/geist-ui/after/pulse-variants.png) |
| Выбранные | [до](screenshots/geist-ui/before/current-selected.png) / [после](screenshots/geist-ui/after/current-selected.png) | [до](screenshots/geist-ui/before/pulse-selected.png) / [после](screenshots/geist-ui/after/pulse-selected.png) |
| Редактор | [до](screenshots/geist-ui/before/current-editor.png) / [после](screenshots/geist-ui/after/current-editor.png) | [до](screenshots/geist-ui/before/pulse-editor.png) / [после](screenshots/geist-ui/after/pulse-editor.png) |
| Палитра и вложенный dropdown | [до](screenshots/geist-ui/before/current-palette.png) / [после](screenshots/geist-ui/after/current-palette.png) | [до](screenshots/geist-ui/before/pulse-palette.png) / [после](screenshots/geist-ui/after/pulse-palette.png) |
| Mobile 390×844 | [после](screenshots/geist-ui/after/current-mobile.png) | [до](screenshots/geist-ui/before/pulse-mobile.png) / [после](screenshots/geist-ui/after/pulse-mobile.png) |

У project settings ширина 32 px и вертикальное отклонение от названия 0 px
в обеих темах; длинный проект оставляет место кнопке. Drawer на desktop 880 px,
две колонки по 397.5 px; на mobile — одна. В DOM после прокрутки нижняя граница
последней выбранной карточки не пересекает footer. Материалы переносятся;
бренды сохраняют отдельные grid-строки. Основная кнопка и счётчик помещаются.
Нет нового горизонтального overflow страницы на 390 px. Предпросмотр PDF имеет
собственный существующий zoom/scroll, его изображение не стало UI-текстом Geist.

Дополнительно: [казахские/русские строки Current](screenshots/geist-ui/after/current-glyphs.png),
[Pulse](screenshots/geist-ui/after/pulse-glyphs.png),
[mobile drawer](screenshots/geist-ui/after/mobile-variants.png),
[125% equivalent](screenshots/geist-ui/after/zoom-1.25.png),
[150% equivalent](screenshots/geist-ui/after/zoom-1.5.png),
[заблокированный font](screenshots/geist-ui/after/blocked-font-fallback.png).
125%/150% воспроизведены через эквивалентные CSS viewport/DPR в CDP;
native browser-menu zoom отдельно **NOT TESTED**.

Первый мобильный capture после закрытия временного specimen-dialog содержал
устаревшую поверхность при resize. Приёмка теперь ждёт кадры compositor и
снимает узкий экран после перезагрузки; итоговый capture просмотрен без дефекта.
Однократный WinError 64 тестового proxy при закрытии браузера устранён обработкой
отключившегося клиента. Финальный прогон завершился без этой ошибки.

## Проверки и сохранность

| Проверка | Результат |
|---|---|
| Frontend `npm test` | PASS: 48 tests |
| ESLint / TypeScript / production build | PASS |
| Ruff format/check трёх acceptance scripts | PASS |
| `git diff --check` | PASS |
| Финальная Edge приёмка | PASS: 34 checks, 123 actual-font records |
| Dev / production, F5, смена темы, delay/block/recovery | PASS |
| Бинарные cmap/WOFF2/weight/tnum + Defender | PASS в указанных границах |
| PDF pipeline и зависимости | PASS: 131 исходный SHA-256 без изменений |
| Новая генерация PDF / backend regression suite | NOT TESTED: код и данные этих подсистем не менялись |
| Другие ОС, браузеры, screen reader и native-menu zoom | NOT TESTED |

Проверены hashes renderer/design/layout, PDF-font/assets, контрольных PDF/PNG,
package.json и lockfile: **0 изменённых из 131**. В изолированном UI редакторе
загружены прежние preview-байты; document и collection до/после совпадают.
Проверенный PDF SHA-256:
`5f0517fd5f5fd0ae952792b854414625f82a147a62e528c50f23744bb957d92d`.
Точные hashes обоих preview находятся в
[acceptance.json](screenshots/geist-ui/after/acceptance.json).
ReportLab, style versions, DesignPlan и сохранённые документы не переписывались.

Основная PostgreSQL, backend, поиск, VTO sync и проектные подборки не менялись.
Тестовые записи существовали только в task-owned SQLite. Временные базы, профили
и процессы закрыты; архив и изолированные инструменты аудита удалены. Постоянные
проверки, сравнения и JSON evidence сохранены. PostgreSQL, Ollama, основной API
и production frontend оставлены работающими.

Для повторения UI: из корня `.\.venv\Scripts\python.exe scripts/check_site_theme.py --font-ui`
при готовом production build. Нужен установленный Edge и существующий cache
исходных fixture-фото. Команда создаёт и убирает только собственные ресурсы.
Для бинарного аудита предварительно установить FontTools 4.68.0 и Brotli 1.2.1
через pip `--only-binary=:all: --target .local/geist-font-audit-tools`, затем
запустить `scripts/check_geist_assets.py` и удалить только этот временный target.

Результаты до/после, font network, hashes и scanner receipt доступны в
[папке evidence](screenshots/geist-ui/) и
[verification summary](screenshots/geist-ui/verification-summary.json).
