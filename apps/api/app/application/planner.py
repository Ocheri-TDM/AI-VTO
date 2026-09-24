from dataclasses import dataclass

from app.domain.intent import CATEGORY_TERMS
from app.domain.models import SearchIntent


@dataclass(frozen=True)
class SearchTask:
    query: str
    filters: SearchIntent


class SearchPlanner:
    def plan(self, intent: SearchIntent) -> list[SearchTask]:
        if not intent.categories:
            return [SearchTask(intent.raw_query, intent)]
        return [
            SearchTask(
                CATEGORY_TERMS.get(category, category), intent.model_copy(update={"categories": [category]})
            )
            for category in dict.fromkeys(intent.categories)
        ]
