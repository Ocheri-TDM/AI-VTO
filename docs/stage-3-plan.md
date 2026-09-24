# Stage 3: Product Selection Workspace

## Аудит

Stage 2 использует useChat, same-origin API client, POST SSE, PostgreSQL selection,
SearchStateHistory и current_view. Эти границы сохраняются. Текущий интерфейс имеет
локальную сортировку, placeholder sidebar, выбранные только внутри текущего view,
нет active filters DTO, pool count, can_undo и UX истёкшей сессии. Composer не
поддерживает Enter/send; desktop chat занимает место слева вместо самостоятельной
правой панели. Технические детали открыты сразу.

## Контракт

Добавить workspace DTO к ChatResponse: active state, pool count, selected cards,
filter facets, can_undo. GET chat возвращает project id и expired flag. Typed workspace
PATCH выполняет существующие tools под тем же chat lock, без LLM и без browser.
GET/PATCH/DELETE projects используют существующие таблицы в local single-user scope.
Selected/not-selected добавляется в ViewFilters JSONB, новая таблица не нужна.
Undo availability определяется реальной history, не номером версии.

## Frontend

Сохранить React hooks/API/SSE, расширить useChat для project switch, authoritative
response, optimistic selection с rollback и защиты от устаревших ответов.
Отдельные layout, projects, workspace, filters, chat, common компоненты. CSS variables
в tokens.css; shell: sidebar 240px, гибкий grid, chat 360px. Независимый scroll.
Tablet сворачивает панели, mobile (<768px) показывает нижнюю навигацию и native dialog
drawers с focus trap/Escape. Карточки и details переиспользуются с улучшением состояний.

## Проверка

Backend contract tests и регрессии Stage 2. Frontend Chromium tests с API fixtures
только в тестах: 20 сценариев задания, ошибки/rollback/SSE/project switch/expiry.
Скриншоты 1920,1440,1280,768,430,390px; отдельный live workflow Gifts/Ucontay/Qwen.
Lint/typecheck/production build. Отчёт с известными ограничениями и Stage 4 boundary.
