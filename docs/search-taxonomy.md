# Open product taxonomy — Stage 4

## Root cause «Карандаши → бутылки»

Воспроизведён вызовом ResearchPlanner.parse без базы и без модели. Старый CategoryResolver не знал карандаши. Любая неизвестная фраза передавалась в CategoryDiscovery, чей общий fallback начинался с bottle/notebook/pen. Planner затем генерировал bottle queries. Таким образом, ошибка возникала до supplier retrieval; frontend fixtures, cache и Qwen не являлись её первопричиной. Второй найденный случай: новый ambiguous запрос в активном чате трактовался как refinement старой категории.

Добавлены failing regression tests, затем исправление. Теперь неизвестный предметный запрос сохраняет dynamic concept, а broad discovery включается для признаков задачи: мерч, конференция, подарки. Независимый запрос создаёт новый ResearchIntent/ResearchSession, без прежних colors, budget, coverage и selection. Фоновое обновление не меняет активное исследование: latest определяется created_at, а не временем обновления старой сессии.

## Модель и resolution

`ProductTaxonomyNode`: id, canonical_name, display_name_ru, aliases, parent_id, supplier_mappings. UniversalCategoryResolver принимает дополнительные nodes; начальные seed-узлы не являются закрытым enum. Русские корни покрывают карандаш/карандаши/карандашей; generic search использует PostgreSQL Russian FTS. Термос отделён от термокружки. Для неизвестного термина создаётся стабильный `dynamic:<hash>` и сохраняется исходный текст, не UNKNOWN_CATEGORY.

QueryExpansion использует aliases только соответствующего concept. ResearchCoverage хранит query_origin, query_reason и query_confidence. В текущем MVP expansions словарные; свободная генерация taxonomy моделью не разрешена. Для нестандартной семантики refinements используется Qwen, для простых категорий LLM не требуется.

ProductCategoryMatcher отдельно проверяет заголовок и supplier breadcrumbs/category. Описание само по себе не доказывает exact category: бутылка, в описании которой упомянут карандаш, не является карандашом. Упаковка и наборы имеют дополнительный guard. Модель предусматривает EXACT/RELATED/EXPLORATORY/REJECTED; strict query допускает только EXACT. RELATED/EXPLORATORY пока не выводятся отдельной выдачей.

Generic matching остаётся консервативным: морфология и title/path evidence, не произвольное семантическое равенство. EmbeddingProvider оставлен как interface. При текущем небольшом индексе отдельные pgvector/model не добавлены: стоимость и операционная сложность пока не подтверждены измерениями. Это ограничивает понимание редких синонимов, но не запрещает поиск нового термина.

## Regression coverage

Тестируются карандаши, ручки, зонты, рюкзаки, термосы, блокноты, powerbank; неизвестный «антистресс»; изоляция цвета и категорий; ложные упоминания и футляры; новый IT-запрос после другой категории; сохранение active research при refresh старой сессии.
# Final additions

Supplier-first discovery is independent of taxonomy seeds. Unmapped products are retained and receive reviewable TaxonomySuggestion records when a supplier label is present. Qwen cannot approve or mutate those mappings. Conservative grouping now requires brand and multiple physical facts in addition to model tokens; false merges are preferred to be avoided even at the cost of duplicate display. Charging aliases include both `зарядка` and `зарядное устройство`. The reproducible final query dataset lives in apps/api/tests/fixtures/final_queries.json.
