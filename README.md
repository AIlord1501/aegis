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
