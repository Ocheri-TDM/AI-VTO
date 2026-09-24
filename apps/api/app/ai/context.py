from pydantic import BaseModel

from app.domain.models import SearchSnapshot
from app.domain.search_state import SearchState, current_view


class ProductSummary(BaseModel):
    id: str
    name: str
    category: str | None
    price_kzt: int | None
    colors: list[str]
    score: float


class ContextBuilder:
    def build(self, chat: dict, snapshot: SearchSnapshot | None) -> dict:
        result = {
            "project": chat.get("project"),
            "recent_messages": [
                {"role": m["role"], "content": m["content"][:500]} for m in chat.get("messages", [])[-8:]
            ],
            "previous_decisions": chat.get("decisions", [])[:2],
            "last_intent": chat.get("last_intent"),
            "session": None,
        }
        if snapshot:
            state = SearchState.model_validate(snapshot.state)
            compact_state = state.model_dump(mode="json")
            compact_state["selected_ids"] = state.selected_ids[:24]
            compact_state["filters"]["hidden_ids"] = state.filters.hidden_ids[:24]
            compact_state["hidden_count"] = len(state.filters.hidden_ids)
            view = current_view(snapshot.products, snapshot.intent, state)
            result["session"] = {
                "id": snapshot.id,
                "intent": snapshot.intent.model_dump(mode="json"),
                "state": compact_state,
                "coverage": snapshot.coverage,
                "expires_at": snapshot.expires_at.isoformat(),
                "total_count": len(snapshot.products),
                "visible_count": len(view),
                "selected_count": len(state.selected_ids),
                "products": [
                    ProductSummary(
                        id=x.product.id,
                        name=x.product.name[:160],
                        category=x.product.category,
                        price_kzt=x.product.price_kzt,
                        colors=[c.normalized_color.value for c in x.product.colors],
                        score=x.score,
                    ).model_dump()
                    for x in view[:24]
                ],
            }
        return result
