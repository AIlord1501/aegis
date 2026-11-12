import pytest

from app.core.config import Settings
from app.llm.service import DeterministicEmbeddingModel, LLMService
from app.llm.types import ModelResult, ModelUsage
from app.services.text import chunk_text, content_hash


class FakeStructuredModel:
    provider = "fake"
    model = "fake-json"

    async def generate_json(self, prompt: str) -> ModelResult:
        assert "verified" in prompt
        return ModelResult({"status": "ok"}, self.provider, self.model, 3, ModelUsage(10, 5))


@pytest.mark.asyncio
async def test_provider_independent_llm_service_supports_structured_output():
    service = LLMService(settings=Settings(embedding_provider="deterministic"), model=FakeStructuredModel(), embeddings=DeterministicEmbeddingModel())
    result = await service.generate_json('{"verified": true}')
    vector = await service.embed("database timeout")
    assert result.output == {"status": "ok"}
    assert result.provider == "fake"
    assert len(vector) == 768


def test_text_cleaning_chunking_and_hashing_are_stable():
    content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_text(content, chunk_size=30, overlap=5)
    assert chunks
    assert content_hash(content) == content_hash(content)
    assert all(chunk.strip() == chunk for chunk in chunks)
