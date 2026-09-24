"""Model-independent JSON-schema completion. HTTP transport never executes tools."""

import json
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.decision import AgentDecision, NextStep
from app.observability import log_event

PROMPTS = Path(__file__).parent / "prompts"


def decoding_schema(value):
    """Keep JSON shape/enums; enforce large bounds in Pydantic after decoding.

    llama.cpp expands bounded repetitions into grammar rules. UI-sized limits
    (e.g. 2000 hidden IDs) can exceed its grammar complexity cap.
    """
    if isinstance(value, dict):
        return {
            key: decoding_schema(item)
            for key, item in value.items()
            if key
            not in {
                "maxItems",
                "minItems",
                "maxLength",
                "minLength",
                "maximum",
                "minimum",
                "default",
                "title",
            }
        }
    if isinstance(value, list):
        return [decoding_schema(item) for item in value]
    return value


class ModelUnavailable(Exception):
    pass


class LocalLLMProvider(Protocol):
    async def decide(self, context: dict, message: str) -> AgentDecision: ...
    async def next_step(self, context: dict, results: list[dict]) -> NextStep: ...


class StructuredLocalProvider:
    def __init__(self, base_url: str, model: str, timeout: float = 60, retries: int = 1):
        self.base_url, self.model, self.timeout, self.retries = base_url.rstrip("/"), model, timeout, retries

    async def complete(self, messages: list[dict], schema: type[BaseModel]):
        if not self.model:
            raise ModelUnavailable("LLM_MODEL_NOT_CONFIGURED")
        messages = list(messages)
        transport_schema = decoding_schema(schema.model_json_schema())
        messages.insert(
            1,
            {
                "role": "system",
                "content": "Return JSON matching this schema: "
                + json.dumps(transport_schema, ensure_ascii=False, separators=(",", ":")),
            },
        )
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                    raw = await self.request(client, messages, transport_schema)
                return schema.model_validate_json(raw)
            except (httpx.HTTPError, ValidationError, KeyError, TypeError, ValueError) as exc:
                log_event("llm_structured_error", attempt=attempt + 1, error_type=type(exc).__name__)
                repair = (
                    [{"path": ".".join(map(str, e["loc"])), "error": e["type"]} for e in exc.errors()]
                    if isinstance(exc, ValidationError)
                    else []
                )
                messages.append(
                    {
                        "role": "user",
                        "content": "Invalid structured response. Return only valid JSON matching the supplied schema. Do not invent product data. Validation errors: "
                        + json.dumps(repair),
                    }
                )
        raise ModelUnavailable("LLM_INVALID_OR_UNAVAILABLE")

    async def decide(self, context: dict, message: str) -> AgentDecision:
        return await self.complete(
            [
                {
                    "role": "system",
                    "content": (PROMPTS / "orchestrator_system.md").read_text(encoding="utf8")
                    + "\n"
                    + (PROMPTS / "intent_parser.md").read_text(encoding="utf8"),
                },
                {
                    "role": "user",
                    "content": json.dumps({"context": context, "message": message}, ensure_ascii=False),
                },
            ],
            AgentDecision,
        )

    async def next_step(self, context: dict, results: list[dict]) -> NextStep:
        return await self.complete(
            [
                {
                    "role": "system",
                    "content": (PROMPTS / "orchestrator_system.md").read_text(encoding="utf8")
                    + "\nTools already executed. Return done=true unless a distinct remaining part of the latest user request requires one more action. Never repeat search, refresh, undo or selection.",
                },
                {
                    "role": "user",
                    "content": json.dumps({"context": context, "tool_results": results}, ensure_ascii=False),
                },
            ],
            NextStep,
        )


class OllamaProvider(StructuredLocalProvider):
    async def request(self, client, messages, schema):
        response = await client.post(
            self.base_url + "/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "format": schema,
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
            },
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


class OpenAICompatibleProvider(StructuredLocalProvider):
    async def request(self, client, messages, schema):
        base = self.base_url if self.base_url.endswith("/v1") else self.base_url + "/v1"
        response = await client.post(
            base + "/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 2048,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "agent_decision", "schema": schema, "strict": True},
                },
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
