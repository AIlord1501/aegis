# Aegis — Phase 1

Aegis is a FastAPI-based engineering operations foundation. Phase 1 contains authentication and user-owned services/incidents. Phase 2 adds a Redis-backed observability ingestion edge and a Go persistence processor. AI agents are intentionally out of scope.

## Repository structure

```text
app/
  api/       HTTP routers and dependency injection
  core/      settings, password hashing, and JWTs
  db/        async SQLAlchemy engine/session and declarative base
  models/    database entities
  schemas/   Pydantic request/response contracts
alembic/     async migration environment and initial migration
tests/       async API and authorization tests
processor/   Go Redis Streams consumer and PostgreSQL persistence worker
cmd/         Go processor executable
docker-compose.yml local PostgreSQL, Redis, API, and processor services
app/llm/     provider-independent structured LLM and embedding clients
evaluation/  representative retrieval evaluation dataset
scripts/     reproducible evaluation utilities
app/agents/  LangGraph graph, bounded agents, and permissioned tools
```

## Windows development setup

Install Python 3.11+ and PostgreSQL. In PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Create a PostgreSQL database and user matching `.env`, or update `DATABASE_URL`. Start Redis locally as well. Replace `JWT_SECRET_KEY` with a long random value; never commit `.env`.

Run the schema migration and API:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

For the complete local stack, Docker Desktop is the simplest Windows path:

```powershell
$env:JWT_SECRET_KEY = "replace-with-a-long-random-secret"
docker compose up --build
docker compose exec api alembic upgrade head
```

The API is available at `http://localhost:8000`, the processor health endpoints at `http://localhost:8081/healthz` and `/readyz`, PostgreSQL at port `5432`, and Redis at port `6379`. The API container needs the migration command run once after the database becomes healthy.

## Phase 3: AI Intelligence Engine

Phase 3 adds:

- `POST /api/v1/repositories` and `POST /api/v1/repositories/{id}/index` for owner-scoped GitHub connections and incremental code/document indexing.
- `POST /api/v1/knowledge/documents/text` and `POST /api/v1/knowledge/documents/file` for Markdown, text, and PDF ingestion.
- `POST /api/v1/knowledge/search` for service/repository/document/incident-filtered hybrid retrieval.
- `POST /api/v1/incidents/{id}/analysis` plus report retrieval endpoints for structured, citation-bearing investigation reports.
- `GET /api/v1/intelligence/model/health` for configured model health.

Configure local Ollama with:

```powershell
ollama pull llama3.1
ollama pull nomic-embed-text
$env:LLM_PROVIDER = "ollama"
$env:LLM_MODEL = "llama3.1"
$env:EMBEDDING_PROVIDER = "ollama"
$env:EMBEDDING_MODEL = "nomic-embed-text"
```

For a hosted OpenAI-compatible provider, set `LLM_PROVIDER=openai-compatible`, `LLM_API_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, and the corresponding embedding provider/model. API keys are read only from environment variables. If a GitHub token is supplied when connecting a private repository, generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and set `GITHUB_TOKEN_ENCRYPTION_KEY`; the token is encrypted before persistence.

Run the retrieval evaluation against a corpus with `source` and `text` fields:

```powershell
python scripts/evaluate_retrieval.py --corpus evaluation/dataset.json
```

The report includes precision@k, recall@k, context relevance, citation correctness, and placeholders for live model latency/token metrics. Model calls are persisted in PostgreSQL with provider, model, status, latency, and token counts.

## Phase 4: Multi-agent investigations

Phase 4 uses LangGraph for a bounded, typed investigation graph:

```text
triage -> parallel(logs, code, knowledge) -> aggregate -> resolution -> verification
                                                                    |
                                                     approval when remediation is proposed
```

The graph persists investigation state, checkpoints, agent executions, tool executions, and approval records in PostgreSQL. All tools enforce the authenticated user's service/repository ownership. `run_permitted_tests` is intentionally advisory until an isolated test executor is configured; no agent can execute arbitrary shell commands or destructive actions.

Phase 4 endpoints:

- `POST /api/v1/incidents/{incident_id}/investigate`
- `GET /api/v1/investigations`, `/api/v1/investigations/{id}`, `/timeline`, and `/agents`
- `GET /api/v1/approvals`
- `POST /api/v1/approvals/{id}/approve` and `/reject`

Investigations run in a bounded background task with configurable timeouts, retry limits, and iteration limits. Approval resumes the graph at verification; rejection marks the investigation rejected. The final report distinguishes facts, hypotheses, evidence, remediation, verification, and outstanding risks.

Run tests:

```powershell
pytest
```

The tests use an in-memory SQLite database to stay fast and isolated; production configuration uses PostgreSQL through `asyncpg`. All application database access is asynchronous.

## API scope

- `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- `POST/GET/GET by id/PATCH/DELETE /services`
- `POST/GET/GET by id /incidents`
- `POST /services/{service_id}/ingest-key` rotates a service ingestion key; the raw key is returned only in that response.
- `POST /api/v1/events/ingest` accepts a bounded batch of structured events with `X-API-Key`.
- `GET /health`

Every service query includes the authenticated user's `owner_id`. Incident queries join through `services` and apply the same ownership predicate, so an ID alone cannot cross user boundaries.

## Observability pipeline

```text
producer --X-API-Key--> FastAPI --XADD--> Redis stream
                                             |
                              Go consumer group / worker pool
                                             |
                         PostgreSQL INSERT ... ON CONFLICT DO NOTHING
                                             |
                                  XACK after successful persistence
```

The API computes a SHA-256 fingerprint over the normalized service, event identity, timestamp, severity, source, message, and sorted attributes. The Go worker preserves that fingerprint and PostgreSQL's unique constraint makes retries idempotent. Invalid or repeatedly failing messages are written to the configured dead-letter stream and acknowledged so they do not block the consumer group.

Ingestion limits default to 100 events and 1 MiB per request. Existing Phase 1 services created before migration `0002` must rotate/create an ingestion key before they can publish events.
