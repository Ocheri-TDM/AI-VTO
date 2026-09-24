from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.application.tools import SelectInput
from app.domain.models import SearchIntent, SoftPreferences
from app.domain.search_state import ViewFilters


class AgentAction(StrEnum):
    START_RESEARCH = 'START_RESEARCH'
    REFINE_LOCAL = 'REFINE_LOCAL'
    RERANK = 'RERANK'
    SEARCH_SIMILAR = 'SEARCH_SIMILAR'
    TARGETED_RESEARCH = 'TARGETED_RESEARCH'
    EXPAND_RESEARCH = 'EXPAND_RESEARCH'
    REFRESH = 'REFRESH'
    SELECT = 'SELECT'
    SHOW = 'SHOW'
    SEARCH = "SEARCH"
    FILTER_RESULTS = "FILTER_RESULTS"
    SORT_RESULTS = "SORT_RESULTS"
    EXPAND_RESULTS = "EXPAND_RESULTS"
    REMOVE_RESULTS = "REMOVE_RESULTS"
    RESTORE_RESULTS = "RESTORE_RESULTS"
    CHANGE_INTENT = "CHANGE_INTENT"
    REFRESH_SEARCH = "REFRESH_SEARCH"
    SELECT_PRODUCTS = "SELECT_PRODUCTS"
    SHOW_RESULTS = "SHOW_RESULTS"
    SHOW_PRODUCT_DETAILS = "SHOW_PRODUCT_DETAILS"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    NO_ACTION = "NO_ACTION"
    UNDO = "UNDO"
    RESET_FILTERS = "RESET_FILTERS"


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: AgentAction
    intent: SearchIntent | None = None
    filters: ViewFilters | None = None
    sorting: Literal["relevance", "price_asc", "price_desc", "darker"] | None = None
    preferences: SoftPreferences | None = None
    selection: SelectInput | None = None
    product_id: str | None = Field(default=None, max_length=200)
    restore_mode: Literal["undo", "reset", "removed", "budget"] = "reset"
    search_session_id: str | None = Field(default=None, max_length=36)
    reasoning_summary: str = Field(
        default="", max_length=240, description="Brief action justification, not hidden reasoning"
    )
    requires_search: bool = False
    requires_refresh: bool = False
    user_response_hint: str = Field(default="", max_length=300)


class NextStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool = True
    decision: AgentDecision | None = None
