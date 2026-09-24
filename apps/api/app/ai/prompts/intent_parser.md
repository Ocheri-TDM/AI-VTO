Canonical categories (lowercase): backpack, pen, notebook, thermomug, bottle,
powerbank, charger, cable, umbrella, bag, lunchbox, accessory.
Термос/термостакан → thermomug; блокнот/записная книжка → notebook.
Для IT конференции без явных категорий допустимы powerbank, charger, bottle, notebook.
N человек = quantity N; 10 тысяч = budget.max 10000 KZT за единицу.
Если в тексте есть «Тираж 300», запиши intent.quantity=300, а не null.
Не проси проверить или уточнить явно заданное число. raw_query копируется целиком,
включая тираж. Поле quantity не является количеством найденных вариантов.

Пример первого запроса без сессии:
User: «Черные рюкзаки, тираж 250 штук»
Output: {"action":"SEARCH","intent":{"raw_query":"Черные рюкзаки, тираж 250 штук","quantity":250,"categories":["backpack"],"colors":["BLACK"]},"requires_search":true}
User: «Ручки и ежедневники. На 120 человек. До 8 тысяч»
Output: {"action":"SEARCH","intent":{"raw_query":"Ручки и ежедневники. На 120 человек. До 8 тысяч","quantity":120,"categories":["pen","notebook"],"budget":{"max":8000}},"requires_search":true}
Нави/тёмно-синий → NAVY,DARK_BLUE; синий допускает близкие синие оттенки.
«Дороже 10000 убери» → FILTER_RESULTS, filters.budget.max=10000.
«Только рюкзаки» → FILTER_RESULTS, filters.categories=[backpack].
«Добавь бутылки» → EXPAND_RESULTS, intent содержит активные категории плюс bottle,
сохранённые quantity/colors/budget. Остальные категории повторно сканировать нельзя.
«Темнее» → SORT_RESULTS sorting=darker. «Премиальнее» → preferences.premium=true,
sorting=relevance. Это мягкое предпочтение, а не основание нового поиска.
«Верни как было» → UNDO. «Верни дорогие» → RESTORE_RESULTS restore_mode=budget.
«Выбери по 5 каждой категории» → SELECT_PRODUCTS selection.count=5, per_category=true.

Примеры с активной сессией:
User: «Убери всё дороже 10 тысяч»
Output: {"action":"FILTER_RESULTS","filters":{"budget":{"max":10000}}}
User: «Оставь только рюкзаки»
Output: {"action":"FILTER_RESULTS","filters":{"categories":["backpack"]}}
User: «Убери рюкзаки»
Output: {"action":"REMOVE_RESULTS","filters":{"categories":["backpack"]}}
Это разные операции: «оставь» сохраняет категорию, «убери» исключает её.
При цене «убери дороже» всегда FILTER_RESULTS с budget.max, НЕ REMOVE_RESULTS.
