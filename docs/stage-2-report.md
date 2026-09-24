# Отчёт об этапе 2 — AI Orchestrator

Проверено 9–10 сентября 2026 локально на Windows. Реальные товары поступали из
Gifts.ru и Ucontay.kz через Chromium, модель — локальная Qwen3 4B Q4_K_M в Ollama.

## Проверенный диалог

| Сообщение | Действие | Видимых | Выбрано | Обход |
|---|---|---:|---:|---|
| Четыре категории, тёмно-синий, тираж 300 | SEARCH | 24 | 0 | Gifts/Ucontay; повтор использовал кэш |
| Убери дороже 10 тысяч | FILTER_RESULTS | 21 | 0 | Нет |
| Оставь только рюкзаки | FILTER_RESULTS | 6 | 0 | Нет |
| Добавь бутылки | EXPAND_RESULTS | 7 | 0 | Только bottle на двух сайтах |
| Выбери 5 лучших | SELECT_PRODUCTS | 7 | 5 | Нет |
| Верни предыдущий вариант | UNDO | 7 | 0 | Нет |

Один SearchSession ID сохранён на всех шагах. Итоговая выборка: 6 рюкзаков и
1 бутылка. Количество отражает конкретный ограниченный обход, не весь каталог.
Полные ответы и решения сохранены в `.local/stage2/message-0.json` … `message-5.json`.
Сессия временная; эти файлы — локальные артефакты проверки, не источник данных приложения.

Модель самостоятельно успешно разобрала начальный intent через оба HTTP адаптера.
В итоговом диалоге SEARCH, два FILTER_RESULTS и UNDO прошли без fallback;
EXPAND_RESULTS и SELECT_PRODUCTS использовали deterministic fallback после проверки
семантики решения Qwen3 4B. До этой проверки модель путала удаление с сохранением
категории и «5 лучших» с «по 5». Исправление защищает результат, но не делает
маленькую модель безошибочной на любых формулировках.

## Что реализовано

1. **Изменения.** Существующий проект расширен AI orchestration, чатом, coverage,
   состоянием выборки, undo, новым провайдером, миграцией, API и frontend integration.

2. **Переиспользование этапа 1.** Сохранены BrowserEngine, SupplierProvider, Product,
   CurrencyService, AvailabilityService, ColorNormalizer, DeduplicationService,
   SearchService, PostgreSQL repository, API деталей и компоненты карточек.

3. **Oasis.** Код и тесты сохранены. В active composition root OasisProvider не
   создаётся; вместо него включён GiftsProvider. Ucontay не переписывался.

4. **GiftsProvider.** Поиск исследован отправкой реальной формы. Варианты извлекаются
   из JSON-литерала ArticlesData в browser document response; pagination использует
   наблюдённую ссылку next. Для кандидатов открываются точные `/id/` карточки:
   schema.org price/currency, свободный остаток, название, изображения `data-hd`.
   Stock — «Свободно», без резервов и пути. Неоднозначные размерные строки не суммируются.
   RUB конвертирует CurrencyService по настроенному курсу **5.2**, ROUND_HALF_UP.

5. **Orchestrator.** ContextBuilder → structured LocalLLMProvider → validated
   AgentDecision → closed ToolRegistry → deterministic SessionTools → ответ/SSE.
   Нет произвольного браузерного управления моделью.

6. **AgentDecision.** Pydantic, extra=forbid; enum действий, intent, filters,
   sorting, preferences, selection, product_id, session ID, краткое объяснение и flags.
   Flags не заменяют проверку coverage и разрешения явного refresh.

7. **Tools.** search_products, incremental_search, filter_products, sort_products,
   rank_products, select_products, restore_products, refresh_search,
   get_search_summary, get_product_details. Каждый имеет input schema и ToolResult.

8. **Coverage.** Supplier/category, время фактической попытки, ограничения цвета/
   количества, pages_scanned, completed/limited/failed. Не начатые категории не
   считаются просмотренными, limited не означает полный каталог.

9. **Решение о поиске.** Local view actions не используют browser. Для SEARCH/EXPAND
   сравниваются requested coverage и fresh entries. Уже проверенные пары не обходятся
   повторно в течение TTL; явный refresh допускает повторный обход.

10. **Incremental.** Только недостающие пары supplier/category отправляются провайдерам,
    результаты объединяются и дедуплицируются в той же chat SearchSession. Срок старых
    наблюдений не продлевается новым поиском.

11. **SearchSession.** Исходный pool в CachedProduct, фильтры/сортировка/выбор в JSONB state.
    Текущая выборка вычисляется отдельно. Shared cache копируется в изолированную
    chat session перед изменениями, сохраняя исходный expires_at. TTL — 60 минут.

12. **Undo.** SearchStateHistory сохраняет до 20 прошлых состояний и intent без копий
    товаров. Несколько tools одного сообщения объединяются в одну точку отмены.
    Версии монотонны, запись защищена row lock и проверкой версии.

13. **Контекст.** Последние 8 сообщений с ограниченной длиной, project, intent,
    state/coverage summary, 2 предыдущих решения и максимум 24 ProductSummary.
    Полные HTML, metadata, изображения, описания и browser logs не передаются.

14. **Qwen/Llama.** OllamaProvider `/api/chat` и OpenAICompatibleProvider
    `/v1/chat/completions` проверены с настоящей Qwen3 4B. Имя и URL меняются через
    env. Portable Ollama и модель размещены в `.local`, не входят в исходный код.
    Сложные grammar limits проверяются Pydantic после генерации, чтобы избежать
    обнаруженной ошибки llama.cpp при больших maxItems/maxLength.

15. **Ограничения tools.** До 5 вызовов, общий timeout, максимум один дополнительный
    model step для явной составной команды. Нет повторных действий, произвольных
    expressions, shell, SQL, файлов, JS или изменения провайдеров. Refresh требует
    прямой просьбы пользователя.

16. **Защита от выдуманных товаров.** Модель не создаёт Product. ID проверяется по
    активной сессии; выбор также требует присутствия в current view. Факты ответа
    формирует сервер из tool results. Дополнительно проверяется смысл простых команд.

17. **API.** Добавлены POST `/api/chats`, GET `/api/chats/{id}`, POST
    `/api/chats/{id}/messages`, POST `/api/chats/{id}/messages/stream`, POST
    `/api/chats/{id}/selection`. Старые search/details/refresh/trace endpoints сохранены.
    OpenAPI доступна в `/docs` и `/openapi.json`.

18. **Frontend.** Постоянный диалог, загрузка истории после reload, semantic progress
    через SSE, ручной/AI выбор, кнопка undo. Карточки показывают изображение, название,
    целую цену KZT, цвет/категорию и checkbox. Stock/supplier/article остаются в деталях.
    Desktop/mobile проверены Chromium; изображения проверены после загрузки CDN.
    Повторный финальный запуск UI-проверки не нашёл карточки после удаления сессии
    по TTL. История чата сохранилась; скрипт теперь явно диагностирует истёкшую сессию.
    Для повторения UI-проверки сначала нужен свежий прогон `check_stage2.py`.

19. **Тесты.** Добавлены dialogue scenarios, coverage/TTL, cache isolation,
    history/undo, unknown IDs/tools, model fallback/repair/schema transport,
    bounded loop, selection, chat API/SSE/persistence, Gifts normalization.
    Регрессии этапа 1 сохранены; PostgreSQL проверяется отдельно от SQLite fixtures.
    Итоговый прогон: **88 passed, 3 deselected** (live tests запускаются отдельно).
    Ruff, frontend lint/typecheck и production build прошли. Живой диалог и проверка
    frontend выполнялись отдельными acceptance-скриптами.

20. **Env.** LLM_BACKEND, LLM_BASE_URL, LLM_MODEL активированы; добавлены
    LLM_TIMEOUT_SECONDS, LLM_STRUCTURED_RETRIES, MAX_TOOL_CALLS_PER_MESSAGE,
    AGENT_TIMEOUT_SECONDS, DEBUG_AGENT. Рабочая `.env`: Ollama/Qwen3 4B и курс 5.2.
    DEBUG_AGENT по умолчанию выключен; обычный UI не отображает debug metadata.

21. **Windows запуск.** Из корня подготовленной рабочей папки:

    ```powershell
    .\.venv\Scripts\python.exe scripts/local_db.py start
    .\.venv\Scripts\python.exe scripts/local_llm.py
    .\.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
    .\.venv\Scripts\python.exe -m app
    # Во втором терминале:
    npm.cmd --prefix apps/web run dev
    ```

    Frontend `http://127.0.0.1:3000`, API `http://127.0.0.1:8000`, Ollama `11434`,
    локальная БД `55432`. Один API worker, без reload — Windows Proactor для Playwright.
    На новой машине используйте инструкции установки в README; local_* используют
    уже подготовленные portable binaries. Миграция `0002` применена, Alembic check
    не обнаружил расхождений схемы и моделей.

22. **Этап 3.** Полноценные проекты/auth, надёжная очередь/worker, handoff CAPTCHA,
    object color analysis и удаление фона, более сильная модель/семантические evals,
    перепроверка остатков/цен перед презентацией и несколько вариантов PDF/JPG.
    Сейчас нет генератора презентаций, PPTX, регистрации поставщиков или master-каталога.

Подробная архитектура: [ai-orchestrator.md](ai-orchestrator.md).
Наблюдения сайта: [providers/gifts.md](providers/gifts.md).
