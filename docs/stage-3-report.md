# Stage 3 — Product Selection Workspace

## Изменения и решения

1. **UX-проблемы.** Placeholder проектов, локальная сортировка, выбор только внутри
   видимой выборки, отсутствие панели фильтров, непонятное истечение TTL, два
   несвязанных блока вместо workspace. Не было Enter/send и отдельного mobile flow.
2. **Reuse Stage 2.** Сохранены Orchestrator, SearchService, providers, BrowserEngine,
   PostgreSQL, current_view, selection tool, history, API client, SSE parser, useChat,
   ProductCard и ProductDetails. Qwen/Ollama и Gifts/Ucontay не заменены mocks.
3. **Frontend architecture.** React hooks без нового state manager. useChat координирует
   server state, проекты, сообщения и optimistic selection. Вынесены filters, projects,
   common Sheet, workspace intro/status/selected view. Переводы/деньги централизованы.
4. **Компоненты.** Дерево ниже. Старые legacy components/hooks сохранены для этапа 1;
   новый Workspace не использует локальные filters/sort из прежнего ResultsPanel.
5. **App Shell.** Общий topbar, сворачиваемая sidebar, центральная область, правый чат.
   tokens.css: цвета, поверхности, отступы, радиусы, motion, shadows, z-index.
6. **Desktop.** Sidebar 240px, chat 350px (390px на широком экране), гибкая сетка.
   Sidebar, grid и история чата прокручиваются независимо. Панели можно свернуть.
7. **Mobile.** Нижняя навигация «Подборка / Выбрано / AI / Проекты». Chat и projects —
   полноэкранные dialogs, filters — sheet, selected — отдельная панель. Основной экран
   прокручивается естественно; drawer имеет собственный scroll и composer.
8. **ProductCard.** Большая квадратная image surface, целая цена KZT, название,
   человеческий цвет/категория, checkbox 44px. Hover, selected, disabled, loading image,
   image error. Оригинальные фото, без удаления фона; готово к прозрачным изображениям.
   Details: фотографии, описание и свёрнутая техническая информация.
9. **Filters.** Категории, цвета с swatch, min/max целыми KZT, selected/unselected.
   Локальный draft только до Apply; сохранённые условия всегда приходят с сервера.
   Chips очищают соответствующий серверный фильтр; reset сохраняет требование тиража.
10. **Синхронизация.** ChatResponse содержит active_state, state_version, pool_count,
    facets, selected_products, can_undo. Null-фильтры явно сохраняются даже в SSE.
    После AI-команды, ручного изменения, undo и reload UI использует один контракт.
11. **Selection.** Существующий POST selection. Checkbox оптимистичен; ошибка откатывает
    состояние и показывает discreet feedback. Массовые операции: все видимые, снять
    выбор, N лучших. Автоматического выбора при поиске нет.
12. **Reload.** localStorage хранит только active chat ID. Проект, сообщения, сессия,
    фильтры и выбор загружаются с backend. Удалённая/истёкшая сессия не воскресает.
13. **Selected view.** Сервер возвращает выбранные карточки из всего pool, включая
    скрытые текущими фильтрами. Снятие/очистка выбора, категории, средняя цена и диапазон.
    Стоимость всего проекта не рассчитывается.
14. **Chat.** Существующий stateful chat, multiline autosize, Enter/send и Shift+Enter,
    блокировка повторной отправки, сохранение draft при закрытии панели, scroll position.
    Contextual suggestions подставляют текст; отправка требует действия пользователя.
15. **SSE.** Одна обновляемая activity card: анализ, поиск, incremental, filter, sort,
    ranking, selection. Technical reasoning и raw events не показываются как сообщения.
16. **Incremental.** Используется прежняя coverage policy и missing-category search.
    UI получает обновлённый pool, view и filters после завершения; текущие карточки
    остаются доступны для просмотра во время поиска.
17. **Undo.** PATCH workspace вызывает restore_products под тем же chat lock. Доступность
    определяется наличием SearchStateHistory, а не значением version. Несколько tools
    одной ручной операции дают одну точку отмены. Chat-команды продолжают работать.
18. **Projects.** Список из PostgreSQL, создание через POST chats, rename/client settings,
    переключение и удаление. Подтверждение только для удаления проекта с диалогом.
    Локальный single-user scope использует Project.user_id IS NULL; fake login отсутствует.
19. **API.** Добавлены GET `/api/projects`, PATCH/DELETE `/api/projects/{id}`,
    PATCH `/api/chats/{id}/workspace`. Последний принимает state_version, filters,
    sorting либо restore; не принимает JS/URL/SQL и не вызывает LLM/browser. Existing
    routes сохранены. Новые JSONB поля не требуют миграции; Alembic check без drift.
20. **409/410/429.** Typed ApiError и централизованный feedback. 409 сообщает о конфликте
    и перечитывает состояние после ручного PATCH; 410 переводит в expired UX; 429
    предлагает повторить позже. Network errors не стирают сохранённый active chat ID.
21. **Partial failures.** Компактное раскрываемое уведомление, подтверждённые карточки
    остаются в grid. Исключения, source IDs и внутренние логи не выводятся.
22. **Accessibility.** Native dialog focus trap, Escape, возврат фокуса, aria labels,
    skip link, keyboard checkboxes, visible focus, touch controls, reduced-motion.
    Имена товаров ограничены визуально двумя строками, полное имя доступно в details.
23. **Breakpoints.** 1700px — просторный grid/chat; 1280px — компактнее боковые панели;
    1100px — чат в drawer; 768px — мобильная навигация и две колонки карточек.
24. **Frontend tests.** Playwright Python использует существующий Chromium toolchain.
    21 группа сценариев покрывает все 20 обязательных UX случаев, а также image failure,
    technical details и top-N. API fixtures существуют только в тестовых route handlers.
25. **Visual validation.** Проверены 1920×1080, 1440×900, 1280×800, 768×1024,
    430×932, 390×844. Скриншоты `.local/stage3/`; проверены overflow, mobile dialogs,
    фокус, длинные названия, selection, loading и отсутствие page errors.
26. **Проверки.** Backend: 89 passed, 3 live deselected; Chromium fixture suite:
    21 grouped scenarios, также на production server. Alembic check: no new upgrade
    operations. Ruff, ESLint, TypeScript и Next production build прошли. После
    последних изменений API отдельно повторены workspace/chat tests: 2 passed.
27. **Ограничения.** Один локальный пользователь/worker. Данные и выбор живут до TTL
    сессии (60 минут), история чата сохраняется дольше. Providers ограничивают объём
    обхода; Qwen3 4B может использовать Stage 2 fallback. Кнопка cancel пока не добавлена.
    Нет фоновой синхронизации нескольких одновременно открытых вкладок, виртуализации
    больших каталогов, image background removal или загрузки logo. Источники фото могут
    отвечать ошибкой; предусмотрен placeholder. Рекомендуемые и «по соответствию»
    используют один deterministic ranking, поэтому объединены в один пункт сортировки.
28. **Stage 4 boundary.** PresentationDraftInput: project_id, search_session_id,
    selected_product_ids, optional client/project name и logo_asset_id. Это только
    контракт будущей передачи. Генератора презентаций, PDF/JPG/PPTX, image generation нет.

## Живой acceptance: 15 сентября 2026

Проект **Halyk Tech Gifts**, production Next.js, FastAPI/PostgreSQL, локальная
Qwen3 4B через Ollama, реальные Gifts.ru и Ucontay.kz. Mock routes не использовались.

| Шаг | Pool | Показано | Выбрано |
|---|---:|---:|---:|
| Первый поиск, 4 категории, navy, 300 шт. | 128 | 24 | 0 |
| Ручной выбор двух товаров | 128 | 24 | 2 |
| Убери дороже 10000 | 128 | 21 | 2 |
| Оставь только рюкзаки | 128 | 5 | 2 |
| Добавь бутылки | 149 | 6 | 2 |
| Выбери 5 лучших | 149 | 6 | 5 |
| Undo | 149 | 6 | 2 |
| Reload | 149 | 6 | 2 |

Все шаги сохранили один SearchSession. AgentExecution audit подтвердил tools:
search, два manual select, два filter, incremental search, select. Ручной Undo
выполнен через workspace API. Повторного полного обхода для фильтров/выбора не было.

Дополнительно на **390×844** проверены отправка «Покажи сначала самые дешевые»,
фильтр до 5000, Undo до 10000, сохранение двух выбранных после reload и восстановление
прокрутки чата при закрытии/открытии drawer. Найденная ошибка первоначального scroll
исправлена: позиция восстанавливается после открытия native dialog, скрытая панель
не перезаписывает её нулём.

Артефакты: `.local/stage3/live/01-search.json` … `08-reload.json`, `tool-audit.json`,
`desktop.png`, `mobile.png`, `mobile-chat.png`, `selected.png`. Fixture screenshots
и результаты тестов отдельно в `.local/stage3/`. Данные сессии всё равно истекают
через час; эти файлы являются отчётами проверки, не источником товаров приложения.

## Дерево frontend

```text
apps/web/src/
  app/                       layout, page, globals.css, tokens.css, studio.css
  components/
    workspace.tsx            composition shell
    product-card.tsx          reusable card
    product-details.tsx       details dialog / gallery
    search-panel.tsx          stateful chat UI
    common/sheet.tsx
    projects/project-list.tsx
    projects/project-form.tsx
    filters/filter-panel.tsx
    filters/filter-chips.tsx
    workspace/workspace-intro.tsx
    workspace/session-status.tsx
    workspace/selected-view.tsx
  hooks/use-chat.ts           authoritative state / projects / selection / SSE
  lib/api.ts                 typed HTTP + SSE
  lib/types.ts               shared frontend contracts
  lib/format.ts              presentation mappings and prices
apps/web/tests/workspace_browser.py
```

## Команды Windows

```powershell
.\.venv\Scripts\python.exe scripts/local_db.py start
.\.venv\Scripts\python.exe scripts/local_llm.py
.\.venv\Scripts\python.exe -m app
# Другой терминал:
npm.cmd --prefix apps/web run dev

npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run build
# Для production: npm.cmd --prefix apps/web run start

$env:WEB_TEST_URL = "http://127.0.0.1:3000"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe apps/web/tests/workspace_browser.py
.\.venv\Scripts\python.exe scripts/check_stage3_live.py
```

Live script создаёт реальный проект и запускает запросы поставщикам. Не запускать
повторно без необходимости: свежая сессия сохраняет результаты час. Во время live
проверки используйте production server без HMR, иначе изменение кода может оборвать SSE.
