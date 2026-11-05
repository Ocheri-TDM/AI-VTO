# Настройки проекта и панель вариантов модели

5 ноября 2026. Исправлены локальные компоненты интерфейса в темах «Текущая»
и Pulse. Проверка выполнена в установленном Microsoft Edge на production build,
с отдельными SQLite/API и тестовыми проектами. Основная PostgreSQL не использовалась.

## Подтверждённые причины

До изменения кнопка настроек имела `position:absolute; right:0; top:2px`.
В настоящем DOM её правый край находился в **1 px** от внешней границы карточки;
центр был выше центра названия на **5 px**, высота кнопки составляла **29 px**.
Основная кнопка размещала иконку и текст через flex с `align-items:flex-start`.
У настроек не было собственной области в первой строке.

У «Вариантов модели» не было отдельного размера: общий Sheet задавал **480 px**.
Внутри использовалась `.research-grid`, поэтому при viewport 1440 px получались
**три колонки по 132,66–132,67 px**. Изображению внутри его области оставалось
примерно **79 × 79 px**. На мобильном общая сетка продолжала давать две колонки.
Сжатие подтверждено замерами; перекрытие фотографией текста в исходном контроле
Melango не обнаружено и не объявляется первопричиной.

[Исходные DOM-замеры](screenshots/workspace-layout/before/acceptance.json).

## Что изменено

- [project-list.tsx](../apps/web/src/components/projects/project-list.tsx) и
  [studio.css](../apps/web/src/app/studio.css): grid карточки с отдельной
  32 × 32 px кнопкой настроек. Внутри основной кнопки — иконка, название и
  метаинформация; название имеет `minmax(0,1fr)`, ellipsis и полный `title`.
  Кнопки остаются соседними, без вложенных интерактивных элементов.
- [sheet.tsx](../apps/web/src/components/common/sheet.tsx): добавлен только
  опциональный `variant="modelVariants"`. Остальные размеры Sheet сохранены.
- [research.css](../apps/web/src/app/research.css): drawer шириной до **880 px**,
  собственный scroll body и container query по его содержимому. От **572 px**
  содержимого — две колонки; ниже — одна. Отдельная карточка ограничена **440 px**.
  Мобильная панель занимает ширину и высоту viewport. Заголовок не прокручивается.
- [product-card.tsx](../apps/web/src/components/product-card.tsx): прежний
  компонент получил контекст `modelVariants`. Фото пропорционально помещается в
  размеренную область, затем идёт полный текст и прежний checkbox выбора.
  Checkbox перемещён под информацию, чтобы не закрывать предмет. Шрифт названия
  сохранён, цвет и наличие читаются без сокращения смысла. Общая товарная сетка
  и SelectedProducts не получают эти правила.
- [research-workspace.tsx](../apps/web/src/components/research/research-workspace.tsx):
  context передаётся только карточкам drawer. Кнопка открытия остаётся в DOM
  под модальным слоем: прежнее `!family` удаляло её при открытии и мешало вернуть
  клавиатурный фокус после закрытия. Внутри самого drawer повторных кнопок нет.

После изменения settings имеет **32 × 32 px**, внутренний отступ **12 px**
(**13 px** с внешней границей), вертикальное смещение **0 px**. Боковая панель
на desktop осталась **230 px**. Active, hover и клавиатурный focus положение
кнопки не меняют. Метаинформация начинается на одной x-координате с названием.

## Фактические размеры

Одинаковый результат в обеих темах; значения в CSS px. Полосы прокрутки входят
в доступную ширину. В строке 600 px сетка шире карточки, но сама карточка
ограничена 440 px и центрирована.

| Viewport | Drawer | Колонки | Ширина карточки |
|---:|---:|---:|---:|
| 1920 × 1080 | 880 | 2 | 397,5 |
| 1440 × 1000 | 880 | 2 | 397,5 |
| 1024 × 900 | 880 | 2 | 397,5 |
| 760 × 900 | 736 | 2 | 333,5 |
| 600 × 844 | 600 | 1 | 440 |
| 390 × 844 | 390 | 1 | 341 |
| Эквивалент 125%: 1152 × 800, DPR 1,25 | 880 | 2 | 397,5 |
| Эквивалент 150%: 960 × 666, DPR 1,5 | 880 | 2 | 397,5 |

125/150% проверены через соответствующий CSS viewport и DPR в реальном Edge.
Нативный zoom через меню браузера — **NOT TESTED**: отдельный ограниченный probe
показал, что Ctrl+Plus через CDP headless Edge не меняет ни innerWidth, ни DPR.
Эмуляция не выдаётся за успешное изменение нативного zoom. Probe-профиль удалён.

## Приёмка

| Проверка | Результат |
|---|---|
| Frontend component tests | **PASS: 48**, включая 5 новых постоянных регрессий |
| ESLint / TypeScript / production build | **PASS** |
| Ruff acceptance scripts / `git diff --check` | **PASS** |
| Реальный DOM Edge, обе темы и матрица размеров | **PASS: 75 проверок** |
| Короткое, длинное и неразрывное имя; active/inactive; hover/focus | **PASS** |
| Настройки не переключают проект; Escape возвращает фокус | **PASS**, обычное окно настроек сохранило 440 px |
| Четыре Melango и две демонстрационные вариации | **PASS**, порядок сравнивается с API, цвета вручную не сортируются |
| 24 варианта, длинные имя/материал/остаток, отсутствие фото | **PASS**, все 24 доступны, тело прокручивается, заголовок остаётся на месте |
| Один вариант | **PASS: component regression и отдельная DOM boundary fixture**; обычный UI по прежнему контракту не открывает drawer для singleton-группы |
| Фото / информация / checkbox / материал | **PASS**, измерены границы и отсутствие наложений |
| Background и основная сетка | **PASS**, ширина workspace не меняется, scroll остаётся внутри drawer |
| Выбор / снятие / повторное открытие / новый поиск | **PASS**, точные offer IDs сохранены |
| Повторное добавление | **PASS**, тот же ID не увеличивает количество и версию коллекции |
| Панель «Выбранные товары» | **PASS**, прежние 820 px и две колонки на desktop |
| Другие браузеры и screen reader | **NOT TESTED** |
| Backend suite / PDF regeneration | **NOT RUN**, backend и PDF-код не менялись |

Последовательность выбора: красный выбран в основной сетке → отмечен в drawer →
снят там; чёрный добавлен клавишей Space → счётчик 3→4 → повторный add не создаёт
дубликат → закрытие/открытие сохраняет checkbox → снятие возвращает счётчик к 3
и снимает отметку в основной сетке. Затем новый поиск бутылок показывает один
результат и сохраняет те же три точных ID в проектной подборке.

Для одного элемента временная DOM fixture клонировала одну уже отрисованную
карточку; после измерения она удалена. React state и API при этом не менялись.
Она проверяет геометрию одиночной карточки, а не новый пользовательский сценарий.

В раннем ускоренном прогоне немедленное переключение проекта во время hydration
получило существующий admission-ответ «Сервис занят». Финальный сценарий ожидает
окончания чтения проекта перед следующим действием. Backend limits не менялись;
это не нагрузочная проверка быстрого переключения.

## Сравнения

| Сценарий | До | После |
|---|---|---|
| Проекты, тёмная тема | [PNG](screenshots/workspace-layout/before/current-projects.png) | [PNG](screenshots/workspace-layout/after/current-projects.png) |
| Проекты, Pulse | [PNG](screenshots/workspace-layout/before/pulse-projects.png) | [PNG](screenshots/workspace-layout/after/pulse-projects.png) |
| Четыре варианта, тёмная тема | [PNG](screenshots/workspace-layout/before/current-variants.png) | [PNG](screenshots/workspace-layout/after/current-variants.png) |
| Четыре варианта, Pulse | [PNG](screenshots/workspace-layout/before/pulse-variants.png) | [PNG](screenshots/workspace-layout/after/pulse-variants.png) |
| Mobile | [PNG](screenshots/workspace-layout/before/pulse-mobile-variants.png) | [PNG](screenshots/workspace-layout/after/pulse-mobile-variants.png) |

[Длинный список](screenshots/workspace-layout/after/many-variants.png) ·
[Одиночная boundary fixture](screenshots/workspace-layout/after/single-variant-boundary.png) ·
[125%](screenshots/workspace-layout/after/pulse-zoom-125.png) ·
[150%](screenshots/workspace-layout/after/pulse-zoom-150.png) ·
[Все DOM-измерения и checks](screenshots/workspace-layout/after/acceptance.json).

## Данные и воспроизведение

Fixture содержит 4 сохранённых VTO-варианта Melango и 29 явно демонстрационных
товаров. Фотографии чёрного, серого и синего Melango взяты из сохранённого кэша
с проверкой SHA-256. Для красного фото не подставлялось: в этой fixture он
намеренно без локальной фотографии. Числа остатков на screenshots — данные
fixture, не обещание актуального наличия VTO.

```powershell
.\.venv\Scripts\python.exe scripts/check_site_theme.py --workspace-layout
cd apps/web
npm test
npm run lint
npm run typecheck
npm run build
```

Baseline записан до исправления UI через тот же harness с `--before`.
Изолированные SQLite, пользователи/проекты внутри неё, Edge profile, proxy и
API удалены после каждого прогона. Сохранены постоянные tests, scripts, PNG и
JSON измерений. Основные PostgreSQL, Ollama, API и frontend работают. Каталог,
пользовательские проекты/подборки, PDF snapshots, jobs и checkpoints не менялись
этой задачей; миграций и новых browser runtime/dependencies нет.
