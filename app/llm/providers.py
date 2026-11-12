import asyncio
import json
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.llm.types import LLMError, ModelResult, ModelUsage


def _json_content(value: Any, *, provider: str, model: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LLMError("Model returned invalid JSON", provider=provider, model=model) from exc
    if not isinstance(parsed, dict):
        raise LLMError("Model returned a non-object JSON value", provider=provider, model=model)
    return parsed


class OllamaModel:
    provider = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.llm_model
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.timeout = settings.llm_timeout_seconds
        self.max_retries = settings.llm_max_retries

    async def generate_json(self, prompt: str) -> ModelResult:
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/api/chat",
                        json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": "json"},
                    )
                    response.raise_for_status()
                    body = response.json()
                    usage = ModelUsage(body.get("prompt_eval_count"), body.get("eval_count"))
                    return ModelResult(_json_content(body.get("message", {}).get("content"), provider=self.provider, model=self.model), self.provider, self.model, int((time.perf_counter() - started) * 1000), usage)
            except (httpx.HTTPError, ValueError, LLMError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.25 * (2**attempt))
        raise LLMError(f"Ollama request failed: {last_error}", provider=self.provider, model=self.model) from last_error


class OpenAICompatibleModel:
    provider = "openai-compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is required for hosted models", provider=self.provider, model=settings.llm_model)
        self.model = settings.llm_model
        self.base_url = settings.llm_api_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.timeout = settings.llm_timeout_seconds
        self.max_retries = settings.llm_max_retries

    async def generate_json(self, prompt: str) -> ModelResult:
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}},
                    )
                    response.raise_for_status()
                    body = response.json()
                    choice = body.get("choices", [{}])[0]
                    usage_body = body.get("usage", {})
                    usage = ModelUsage(usage_body.get("prompt_tokens"), usage_body.get("completion_tokens"))
                    return ModelResult(_json_content(choice.get("message", {}).get("content"), provider=self.provider, model=self.model), self.provider, self.model, int((time.perf_counter() - started) * 1000), usage)
            except (httpx.HTTPError, ValueError, LLMError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.25 * (2**attempt))
        raise LLMError(f"Hosted model request failed: {last_error}", provider=self.provider, model=self.model) from last_error


class OllamaEmbeddingModel:
    provider = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.embedding_model
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.timeout = settings.llm_timeout_seconds

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/embeddings", json={"model": self.model, "prompt": text})
            response.raise_for_status()
            embedding = response.json().get("embedding")
        if not isinstance(embedding, list):
            raise LLMError("Ollama returned no embedding", provider=self.provider, model=self.model)
        return [float(item) for item in embedding]


class OpenAIEmbeddingModel:
    provider = "openai-compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is required for hosted embeddings", provider=self.provider, model=settings.embedding_model)
        self.model = settings.embedding_model
        self.base_url = settings.llm_api_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.timeout = settings.llm_timeout_seconds

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/embeddings", headers={"Authorization": f"Bearer {self.api_key}"}, json={"model": self.model, "input": text})
            response.raise_for_status()
            embedding = response.json().get("data", [{}])[0].get("embedding")
        if not isinstance(embedding, list):
            raise LLMError("Hosted provider returned no embedding", provider=self.provider, model=self.model)
        return [float(item) for item in embedding]
