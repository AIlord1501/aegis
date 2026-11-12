import hashlib
import math

from app.core.config import Settings, get_settings
from app.llm.providers import OllamaEmbeddingModel, OllamaModel, OpenAICompatibleModel, OpenAIEmbeddingModel
from app.llm.types import EmbeddingModel, LLMError, ModelResult, StructuredModel


class DeterministicEmbeddingModel:
    provider = "deterministic"
    model = "sha256-test-embedding"

    async def embed(self, text: str) -> list[float]:
        values = []
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        for index in range(768):
            value = seed[index % len(seed)] / 255.0
            values.append((value * 2.0) - 1.0)
        magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / magnitude for value in values]


class LLMService:
    def __init__(self, settings: Settings | None = None, model: StructuredModel | None = None, embeddings: EmbeddingModel | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = model or self._build_model()
        self.embeddings = embeddings or self._build_embeddings()

    def _build_model(self) -> StructuredModel:
        if self.settings.llm_provider == "ollama":
            return OllamaModel(self.settings)
        if self.settings.llm_provider in {"openai", "openai-compatible"}:
            return OpenAICompatibleModel(self.settings)
        raise LLMError(f"Unsupported LLM provider: {self.settings.llm_provider}", provider=self.settings.llm_provider, model=self.settings.llm_model)

    def _build_embeddings(self) -> EmbeddingModel:
        if self.settings.embedding_provider == "ollama":
            return OllamaEmbeddingModel(self.settings)
        if self.settings.embedding_provider in {"openai", "openai-compatible"}:
            return OpenAIEmbeddingModel(self.settings)
        if self.settings.embedding_provider == "deterministic":
            return DeterministicEmbeddingModel()
        raise LLMError(f"Unsupported embedding provider: {self.settings.embedding_provider}", provider=self.settings.embedding_provider, model=self.settings.embedding_model)

    async def generate_json(self, prompt: str) -> ModelResult:
        return await self.model.generate_json(prompt)

    async def embed(self, text: str) -> list[float]:
        vector = await self.embeddings.embed(text)
        dimension = self.settings.embedding_dimension
        if len(vector) > dimension:
            return vector[:dimension]
        if len(vector) < dimension:
            return vector + [0.0] * (dimension - len(vector))
        return vector

    async def health(self) -> dict[str, str]:
        try:
            await self.generate_json('Return exactly {"status":"ok"}.')
        except LLMError:
            raise
        return {"provider": self.model.provider, "model": self.model.model, "status": "ok"}
