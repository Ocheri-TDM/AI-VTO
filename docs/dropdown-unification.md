# Общие dropdown-компоненты: «Текущая» и Pulse

5 ноября 2026. Унифицированы переключатель темы, цвет, материал, размер,
представление товаров, бренды и списки редактора. Поисковый backend, импортёр,
схема БД, идентификаторы/нормализация брендов и материалов не изменялись.

## Фактические причины

| Элемент до изменения | Реализация и источник оформления | Popup и тема |
|---|---|---|
| Тема | Native `select`; закрытое поле отдельно оформлялось в `themes.css` | React не создавал строки popup; оформление отличалось от PaletteSelect |
| Цвет, материал, размер | Native `select`; общие правила `.research-shell select` и `.research-inline-filters select` | Отдельного общего popup-компонента не было |
| «Все варианты / По моделям» | Native `select` с собственными размерами `.research-view-setting select` | Та же разница с custom listbox |
| Бренды в HUD | `details/summary`, абсолютно размещённый `div`, настоящие checkbox | Внутри HUD; фон наследовал `--panel`. Не использовал top layer, общий dismiss или общий keyboard-путь |
| Бренды в мобильной панели | Отдельная встроенная группа checkbox | Внутри Sheet; другой набор CSS-правил |
| Стиль, фото, версия, выразительность, свои материалы в редакторе | Native `select`; `.presentation-workspace select` | Отдельный путь относительно библиотеки палитр |
| Направление, характер, применение внутри `.palette-dialog-body` | Custom PaletteSelect: button-combobox/listbox, `popover="manual"` | Popup оставался в DOM модального окна и поднимался в native top layer. Собственные `--ui-popover-*` уже учитывали тему |

Конкретный дефект брендов: `.research-filters label` задавал flex-структуру,
но HUD использует `.research-inline-filters`. Для его строк не было правила,
раскладывающего checkbox, название и количество. Текст мог объединяться в потоке.
Абсолютный popup также имел собственные размеры и слой, отличавшиеся от палитр.

Это смешение native select, details-popover и custom listbox, а не установленный
дефект браузера. Отсутствие наследования темы у существующего PaletteSelect
не подтверждено: его токены уже работали. До изменения native popup отдельно
не снимался в браузере; ошибочное поведение ОС не приписывается ему по догадке.

## Общая основа

В [common/select.tsx](../apps/web/src/components/common/select.tsx) перенесён
существующий подход PaletteSelect, без новых UI-зависимостей:

- `Select`: одиночный выбор, select-only combobox/listbox, typeahead,
  Arrow/Home/End/Page keys, Enter, Tab, Escape, `aria-activedescendant` и отметка.
- `SearchableSelect`: тот же trigger/popup/строки и combobox-поиск материалов.
- `MultiSelect`: popover с подписанным поиском и группой настоящих checkbox.
  Checkbox не вложены в `role=option`. Выбор остаётся множественным.

`PaletteSelect` сохранён как совместимый экспорт общего Select. Его публичные
props и обработчики редактора сохранены. Native поля заменены с сохранением
контролируемого value, подписи и существующего onChange. Общие controls также
поддерживают hidden form values через `name`; использующие их формы не получают
дополнительных submit-действий, trigger всегда `type=button`.

Popup — DOM-потомок своего control, включая Sheet и PaletteExplorer. Portal
не используется. Native popover top layer устраняет clipping без наращивания
z-index; modal focus containment сохраняется. Размер и положение вычисляет
[dropdown.ts](../apps/web/src/lib/dropdown.ts): обычный список ориентируется на
280 px, бренды — 340 px, тема — 160 px; viewport ограничивает ширину, высота
не больше 360 px. Короткому списку разрешена меньшая высота. При нехватке места
снизу список открывается вверх. Resize/scroll/visual viewport обновляют позицию;
прокручивается собственный список. Соседний dropdown закрывает предыдущий.

Брендовая строка имеет три grid-колонки: `18px minmax(0,1fr) auto`. Длинное имя
переносится внутри строки, количество остаётся справа. Выбор не зависит от
поискового текста. «Бренд не указан», исходные values/counts и заблокированные
бренды из запроса сохранены. Фильтры применяются тем же onChange сразу.

При реальной проверке устранены два дефекта новой общей реализации до приёмки:
blur между pointerdown по подписи и активацией checkbox закрывал popup;
программный click одиночного списка не возвращал фокус trigger, что мешало
Escape внутри dialog. Теперь подпись сохраняет фокус до стандартного checkbox
click, а trigger явно получает фокус. Оба пути включены в браузерные сценарии.

## Токены и состояния

[dropdowns.css](../apps/web/src/app/dropdowns.css) задаёт общие control/popup
background, text, border, shadow, hover, selected, disabled, focus и secondary
text. Токены объявлены на корневом элементе и обновляют уже открытый popup.
Тёмная поверхность — `#1C2822`; Pulse — `#FDFDFF` с тёмным текстом.
Pulse использует прежний красный акцент и светлую зелёную selected-подложку;
активный пункт дополнительно имеет рамку, выбранный — отметку. Фокус виден.
Forced-colors не отключается. Палитры карточек и PDF-preview не перекрашиваются.

Удалены отдельные стили прежних PaletteSelect и theme-native-select, чтобы они
не конкурировали с общей основой. Глобальные resets остаются; правила общих
controls явно задают шрифт, цвет, размеры, границы и checkbox-геометрию.

## Проверки

| Проверка | Фактический результат |
|---|---|
| Frontend tests | **PASS: 43**; включая 7 новых постоянных component/geometry tests |
| ESLint, TypeScript, production build | **PASS** |
| Ruff для acceptance scripts, `git diff --check` | **PASS** |
| Установленный Microsoft Edge, раскрытые меню обеих тем | **PASS: 118 проверок**, 39 измерений геометрии |
| Прежний сценарий темы: две вкладки, новый вход, F5, подборка и палитры | **PASS: 45 theme-проверок**; известный независимый logout `400 INVALID_JSON` сохранён в его отчёте |
| Контраст текста строк и количеств | **PASS: 501 образец, минимум 6,75:1**; это не заявление полной WCAG-сертификации |
| Клавиатура, typeahead, Enter/Space/Tab/Escape, возврат фокуса | **PASS** |
| Несколько брендов, поиск/очистка, длинные имена, unknown brand | **PASS** |
| Цвет, материал и размер: реальный onChange → HTTP → сохранённый filter | **PASS**, исходные значения фасетов сохранены |
| F5: тема, бренды, цвет и проектная подборка | **PASS** |
| Смена темы при открытом popup, соседние меню и outside click | **PASS** |
| Mobile 390 px, Sheet, прокрутка modal, верхнее размещение у нижнего края | **PASS** |
| Все 7 типов controls редактора и 3 списка палитр | **PASS**, открыты в обеих темах |
| Документ, настройки палитры, ревизии и коллекция после просмотра | **PASS: полное сравнение ответов API до/после** |
| Другие браузеры, screen reader | **NOT TESTED** |
| Генерация и визуальная перепроверка PDF | **NOT RUN**: renderer/snapshots не менялись, генерация в этом сценарии не запускалась |

Измерения и полный список:
[acceptance.json](screenshots/dropdowns/acceptance.json).

Браузер использовал production frontend и отдельные SQLite/API/loopback proxy.
Основная PostgreSQL в тестах не использовалась. Fixture содержит 4 сохранённых
реальных варианта Melango и 23 явно обозначенных демонстрационных товара.
Длинные названия, sizes и unknown brand — тестовые фасеты, не восстановленные
данные VTO. Материал и две FAILED-ревизии созданы только в disposable документе
для открытия соответствующих controls; screenshot «Ошибка генерации» относится
к этой fixture. Новых render jobs и реальных неудачных PDF нет.

В ранних прогонах немедленный reset после F5 пересекался с двумя hydration GET
и получал существующий `429 USER_REQUEST_LIMIT`. Backend admission не изменялся.
Финальный сценарий ждёт завершения hydration и проверяет сохранение фильтров
отдельно от допуска параллельных запросов. Это не нагрузочная проверка.

## Скриншоты раскрытых меню

Просмотрены 18 финальных PNG, включая обе темы, редактор и мобильные состояния.

| Сценарий | Текущая | Pulse |
|---|---|---|
| Тема | [Открыть](screenshots/dropdowns/current-theme.png) | [Открыть](screenshots/dropdowns/pulse-theme.png) |
| Цвет | [Открыть](screenshots/dropdowns/current-color.png) | [Открыть](screenshots/dropdowns/pulse-color.png) |
| Длинный материал | [Открыть](screenshots/dropdowns/current-material.png) | [Открыть](screenshots/dropdowns/pulse-material.png) |
| Бренды | [Открыть](screenshots/dropdowns/current-brands.png) | [Открыть](screenshots/dropdowns/pulse-brands.png) |
| Длинный бренд | [Открыть](screenshots/dropdowns/current-long-brand.png) | [Открыть](screenshots/dropdowns/pulse-long-brand.png) |
| Редактор | [Открыть](screenshots/dropdowns/current-editor.png) | [Открыть](screenshots/dropdowns/pulse-editor.png) |
| Палитры | [Открыть](screenshots/dropdowns/current-palette.png) | [Открыть](screenshots/dropdowns/pulse-palette.png) |
| Мобильные бренды | [Открыть](screenshots/dropdowns/current-mobile-brands.png) | [Открыть](screenshots/dropdowns/pulse-mobile-brands.png) |

[Мобильная палитра](screenshots/dropdowns/pulse-mobile-palette.png) ·
[Popup у нижнего края](screenshots/dropdowns/bottom-edge.png).

## Воспроизведение и сохранность

```powershell
.\.venv\Scripts\python.exe scripts/check_site_theme.py --dropdowns
cd apps/web
npm test
npm run lint
npm run typecheck
npm run build
```

Harness переиспользует уже работающий frontend либо запускает свой; занятые
acceptance-порты не перехватывает. SQLite, профиль Edge, cookies, временные
логи и процессы этого прогона удаляются по точному task-owned пути. Основные
PostgreSQL/Ollama/API/frontend остаются работающими. Acceptance JSON и PNG —
постоянные доказательства; component tests и повторяемые scripts сохранены.
Реальные пользователи, документы, проекты, каталог, observations и checkpoints
этими проверками не изменялись.
