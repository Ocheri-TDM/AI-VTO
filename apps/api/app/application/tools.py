"""Typed, closed tool registry. No expressions, scripts, URLs or provider settings."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import SearchIntent, SoftPreferences
from app.domain.search_state import ViewFilters
from app.observability import log_event


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchInput(ToolInput):
    intent: SearchIntent


class FilterInput(ToolInput):
    filters: ViewFilters
    mode: Literal["merge", "replace", "remove"] = "merge"


class SortInput(ToolInput):
    sorting: Literal["relevance", "price_asc", "price_desc", "darker"]


class RankInput(ToolInput):
    preferences: SoftPreferences = Field(default_factory=SoftPreferences)


class SelectInput(ToolInput):
    product_ids: list[str] | None = Field(default=None, max_length=2000)
    count: int = Field(default=5, ge=1, le=200)
    categories: list[str] = Field(default_factory=list, max_length=12)
    per_category: bool = False
    mode: Literal["replace", "add", "remove", "clear"] = "replace"


class RestoreInput(ToolInput):
    mode: Literal["undo", "reset", "removed", "budget"] = "undo"


class DetailsInput(ToolInput):
    product_id: str = Field(min_length=1, max_length=200)


class ToolResult(BaseModel):
    name: str
    search_session_id: str | None = None
    visible_count: int = 0
    selected_count: int = 0
    state_version: int = 0
    searched_categories: list[str] = Field(default_factory=list)
    note: str = ""


@dataclass(frozen=True)
class RegisteredTool:
    input_schema: type[ToolInput]
    handler: Callable[[ToolInput], Awaitable[ToolResult]]


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, RegisteredTool] = {}

    def register(self, name: str, schema: type[ToolInput], handler):
        self.tools[name] = RegisteredTool(schema, handler)

    def schemas(self) -> dict:
        return {name: tool.input_schema.model_json_schema() for name, tool in self.tools.items()}

    async def call(self, name: str, arguments: dict) -> ToolResult:
        if name not in self.tools:
            raise ValueError("UNKNOWN_TOOL")
        tool = self.tools[name]
        data = tool.input_schema.model_validate(arguments)
        log_event("agent_tool_started", tool=name)
        result = ToolResult.model_validate(await tool.handler(data))
        log_event("agent_tool_completed", tool=name, visible_count=result.visible_count)
        return result
