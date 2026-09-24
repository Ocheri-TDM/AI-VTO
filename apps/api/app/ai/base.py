from typing import Protocol

from app.domain.models import SearchIntent


class ModelProvider(Protocol):
    """Model-independent boundary. Implementations must return validated intent only.

    LLMs receive user input, never credentials, and cannot execute browser actions.
    Qwen/Llama/Ollama or any local compatible runtime can implement this contract.
    """

    async def parse_intent(self, query: str) -> SearchIntent: ...
