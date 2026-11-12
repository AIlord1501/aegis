from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    def __init__(self, message: str, *, provider: str, model: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


@dataclass(slots=True)
class ModelUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(slots=True)
class ModelResult:
    output: dict[str, Any]
    provider: str
    model: str
    latency_ms: int
    usage: ModelUsage = field(default_factory=ModelUsage)
    status: str = "success"


class StructuredModel(Protocol):
    provider: str
    model: str

    async def generate_json(self, prompt: str) -> ModelResult: ...


class EmbeddingModel(Protocol):
    provider: str
    model: str

    async def embed(self, text: str) -> list[float]: ...
