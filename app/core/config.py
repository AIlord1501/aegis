from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Aegis API"
    debug: bool = False
    database_url: str = "postgresql+asyncpg://aegis:aegis@localhost:5432/aegis"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret_key: str = "development-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    event_stream_key: str = "aegis:events"
    event_max_batch_size: int = 100
    event_max_request_bytes: int = 1_048_576
    llm_provider: str = "ollama"
    llm_model: str = "llama3.1"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    ollama_base_url: str = "http://localhost:11434"
    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    github_api_base_url: str = "https://api.github.com"
    github_token_encryption_key: str | None = None
    knowledge_job_stream: str = "aegis:knowledge:jobs"
    knowledge_chunk_size: int = 1200
    knowledge_chunk_overlap: int = 150
    rag_top_k: int = 8
    embedding_dimension: int = 768
    agent_max_iterations: int = 8
    agent_timeout_seconds: float = 90.0
    agent_max_retries: int = 2
    agent_max_concurrency: int = 3

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
