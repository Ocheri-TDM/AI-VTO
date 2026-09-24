# AI Orchestrator — этап 2

## Архитектура и границы

```text
Message → ContextBuilder → LocalLLMProvider → AgentDecision (Pydantic)
  → bounded Orchestrator → ToolRegistry → deterministic SessionTools
  → SearchService / current_view / ranking / history → PostgreSQL → response + SSE
```

Переиспользованы BrowserEngine, SupplierProvider, CurrencyService, AvailabilityService,
ColorNormalizer, DeduplicationService, SearchRepository, PostgreSQL, FastAPI, карточки
Next.js. В активном composition root только GiftsProvider и UcontayProvider. Oasis
остаётся отдельным модулем с тестами, но текущий поиск его не создаёт.

LLM не получает браузер, shell, SQL, произвольные пути или URL. Решение — строго
AgentDecision с extra=forbid. Product создаётся только из наблюдений провайдера.
Модель выбирает действие; сервер вычисляет цены, проверяет остатки, изменяет фильтры,
ранжирует и формирует фактический ответ. User response hint не используется для
утверждений о товарах. Цепочка рассуждений не транслируется.

## Intent и нормализация

SearchIntent расширен budget.min/max (целые KZT), quantity optional, style, material,
client, event, premium_level, keywords, excluded_categories/colors, sorting,
SoftPreferences. Старый численный budget на входе принимается как budget.max.
Категории в wire format остаются lowercase; uppercase нормализуется.

CategoryResolver поддерживает 12 категорий, словарные алиасы, семантическую категорию
от LLM и генерацию поискового термина поставщику. Для IT/деловых/широких запросов
fallback выбирает ограниченный набор разумных категорий. Это детерминированный
fallback, не выдаваемый за inference. Упаковка под бутылку исключается из BOTTLE.

ColorIntentResolver расширяет BLUE до близких синих оттенков; NAVY/DARK_BLUE — близкая
тёмная группа. Есть primary/acceptable/excluded и tolerance. Команда «темнее» меняет
порядок готовой выборки. ImageColorAnalyzer — только интерфейс анализа цвета объекта,
реальной сегментации изображений в этом этапе нет.

Ranking даёт score 0..1 и match_reasons: категория, цвет, цена, количественный остаток,
keywords и текстовые признаки SoftPreferences. «Премиальный» не означает автоматически
«самый дорогой». Это эвристика, не сертификат качества. AI reranking пока оставлен
отдельной границей; основной выбор воспроизводим без LLM.

## Сессия и TTL

SearchSession хранит intent, state JSONB, coverage JSONB, статусы источников и trace.
CachedProduct — исходный временный pool. В pool остаются проверенные варианты с
вычислимой ценой и численным положительным остатком, даже если они скрыты текущим
цветом, бюджетом или тиражом. В API карточки попадают только после current_view:
stock >= требуемого количества; неизвестный stock исключён на обоих уровнях.

state: filters, sorting, preferences, selected_ids, version, intent_version.
Фильтры не DELETE-ят pool. Важно: legacy POST /searches/{id}/filter по-прежнему
возвращает непостоянное представление; чат использует постоянные typed tools.

Сессии живут максимум 60 минут с создания. Incremental search не продлевает срок
старых наблюдений. Cleanup удаляет expired SearchSession, CachedProduct и history;
Chat/Message ссылки обнуляются, last_intent позволяет явно повторить запрос.
После expiry уточнение по старой выборке предлагает «обнови поиск», не показывает
устаревшие товары. Refresh создаёт новый pool/history boundary: undo не может
восстановить устаревшие остатки.

Кэш поисковых наблюдений может разделяться между чатами, но перед изменениями
создаётся отдельная SearchSession для конкретного чата. Поэтому фильтры одного чата
не меняют выборку другого. Это временная копия наблюдений с исходным expires_at,
не постоянный каталог.

## Coverage и incremental search

CoverageEntry записывается для фактически начатого supplier/category: searched_at,
colors, quantity, pages_scanned и completed/limited/failed. Не начатые категории
не помечаются покрытыми. Ограниченный обход — limited, а не обещание полного каталога.

При добавлении категории вычисляется missing по каждому поставщику. Только эти
пары передаются SearchService, затем результаты объединяются и дедуплицируются в
исходной chat SearchSession. Повторное добавление уже проверенной категории не
вызывает browser. Failed/limited попытки не повторяются автоматически в течение TTL;
пользователь может явно обновить поиск. Для pool colors=[]/quantity=1 обозначает
сохранение всех фактически обнаруженных вариантов; это не утверждение о полноте сайта.

## Undo и хранение диалога

Chat связан с Project, active_search_session_id и last_intent. Message сохраняет обе
стороны диалога. AgentExecution хранит decision, tool_calls, status и timestamps.
SearchStateHistory хранит до 20 последних снимков state + intent без копирования
товаров. Изменение защищено row lock и проверкой версии. Несколько tools в одном
сообщении объединяются в одну точку undo. Версии растут монотонно даже после отмены.
Выбор вручную и через AI сохраняется одинаково. Скрытые выбранные товары остаются
выбранными; начиная с Stage 3 отдельная панель Selected Products получает выбранные
карточки из всего pool, включая скрытые текущими фильтрами.

## Tools и bounded loop

| Tool | Input | Работа |
|---|---|---|
| search_products | SearchInput | Первый поиск / reuse cache |
| incremental_search | SearchInput | Только missing supplier/category |
| filter_products | FilterInput | Merge/replace/remove фильтров |
| sort_products | SortInput | Relevance, price_asc/desc, darker |
| rank_products | RankInput | SoftPreferences и deterministic ranking |
| select_products | SelectInput | IDs либо N лучших, в том числе по категории |
| restore_products | RestoreInput | Undo, reset, removed, budget |
| refresh_search | ToolInput | Явная перепроверка |
| get_search_summary | ToolInput | Проверенные счётчики |
| get_product_details | DetailsInput | Только ID внутри активной сессии |

У каждого tool Pydantic input и ToolResult output. Незнакомые tools, дополнительные
поля, чужие session IDs и выдуманные product IDs отвергаются. Для выбора добавлением
товар должен также присутствовать в current view. Нельзя передать выражение фильтра.
Настройки провайдеров отсутствуют в schema модели. Доступные браузеру main navigation
hosts ограничены gifts.ru/ucontay.kz; CDN изображений читается как часть обычной страницы.

MAX_TOOL_CALLS_PER_MESSAGE <=5, общий timeout, максимум один дополнительный LLM step.
Дополнительный step разрешён только для явно составной команды («затем…», «и выбери…»).
Обычный поиск не может самостоятельно выбирать товары. Для коротких однозначных
команд validate_command проверяет смысл решения: «оставь» не должно удалять категорию,
«до 10000» обязано ограничивать цену, «добавь бутылки» обязано включать новую категорию,
«5 лучших» не означает «по 5 из каждой категории». При несовпадении применяется
детерминированный fallback. Такие ошибки обнаружены при проверке Qwen3 4B.
Повторные tool calls запрещены, последующий step не может снова запустить поиск,
refresh или undo. Structured output проходит JSONSchema и Pydantic. Невалидный ответ
повторяется с repair prompt до LLM_STRUCTURED_RETRIES, затем CommandParser fallback.
Для model unavailable пользователь получает управляемое действие/уточнение, не HTTP500.
Модель не читает полную историю: ContextBuilder передаёт последние 8 сообщений,
2 предыдущих решения, project, intent/state/coverage summary и до 24 ProductSummary.
В summaries нет изображений, описаний, metadata или внутренних остатков.

## API и SSE

POST /api/chats создаёт Project и Chat. GET /api/chats/{id} возвращает последние
50 сообщений и текущее представление. POST /api/chats/{id}/messages принимает
`{"message":"..."}`. POST /api/chats/{id}/selection принимает SelectInput.
POST /api/chats/{id}/messages/stream — SSE поверх fetch POST:
agent.started, intent.parsed, tool.started, supplier.started/completed,
results.updated, agent.completed/error. Heartbeat каждые 10 секунд.

Итог: assistant_message, action, search_session_id, session с безопасными карточками,
result_summary, selected_product_ids, state_version. DEBUG_AGENT=true дополнительно
возвращает краткое decision, tools, coverage, version/fallback. Обычный UI игнорирует
debug. Длительный поиск не блокирует event loop; другой поставщик работает параллельно.
Одновременно в одном чате выполняется одно сообщение (409 при конфликте).

## Local LLM

`LLM_BACKEND=ollama` использует /api/chat, `format` = Pydantic JSONSchema, stream=false.
В транспортной схеме удалены большие численные/размерные ограничения: llama.cpp
не принимает слишком сложную грамматику повторений. Типы, структура и enum сохранены;
полные ограничения проверяет Pydantic после ответа. Генерация ограничена 2048 токенами.
`LLM_BACKEND=openai_compatible` использует /v1/chat/completions и response_format
json_schema. `LLM_MODEL` — произвольное установленное имя Qwen/Llama или другой
совместимой локальной модели. `LLM_BACKEND=basic` — явный offline fallback.

Официальные источники: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs),
[Ollama Windows](https://docs.ollama.com/windows). Облачный сервис не требуется.
Модель загружает оператор; её имя не зашито в domain/application/provider code.
Качество понимания зависит от выбранной модели; fallback покрывает простые команды,
но не заменяет семантическую модель для любых сложных формулировок.

## Ограничения MVP

Один local API worker, без auth/multi-user deployment и фоновой очереди вне процесса.
Результаты зависят от доступности сайта и лимитов карточек. CAPTCHA не обходится.
Stage 3 добавляет локальный менеджер проектов и workspace API поверх этих же моделей;
multi-user авторизация остаётся отдельным этапом.
Исходные фотографии могут загружаться медленно/быть недоступны на CDN поставщика.
Material/style/client/event сохранены в intent; мягкие признаки поддержаны ranking,
полного проверяемого фильтра всех материалов/брендирования ещё нет.
Отдельные prompts для reranking/response writing подготовлены, генерация текста ответа
сейчас детерминированная. Presentation Engine, PDF/JPG/PPTX и обработка фона отсутствуют.
