"""add knowledge, repository, model, and investigation storage"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_intelligence"
down_revision: Union[str, None] = "0002_observability_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE repository_connections (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            full_name VARCHAR(300) NOT NULL,
            default_branch VARCHAR(200) NOT NULL DEFAULT 'main',
            encrypted_token TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_indexed_at TIMESTAMPTZ,
            CONSTRAINT uq_repository_owner_name UNIQUE (owner_id, full_name)
        )
        """
    )
    op.execute("CREATE INDEX ix_repository_connections_owner_id ON repository_connections(owner_id)")
    op.execute(
        """
        CREATE TABLE knowledge_documents (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            service_id INTEGER REFERENCES services(id) ON DELETE CASCADE,
            repository_id INTEGER REFERENCES repository_connections(id) ON DELETE CASCADE,
            incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
            title VARCHAR(300) NOT NULL,
            source_type VARCHAR(40) NOT NULL,
            source_uri VARCHAR(1000) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_knowledge_document_version UNIQUE (owner_id, source_uri, content_hash)
        )
        """
    )
    op.execute("CREATE INDEX ix_knowledge_documents_owner_source ON knowledge_documents(owner_id, source_type)")
    op.execute("CREATE INDEX ix_knowledge_documents_service_id ON knowledge_documents(service_id)")
    op.execute("CREATE INDEX ix_knowledge_documents_repository_id ON knowledge_documents(repository_id)")
    op.execute("CREATE INDEX ix_knowledge_documents_incident_id ON knowledge_documents(incident_id)")
    op.execute(
        """
        CREATE TABLE knowledge_chunks (
            id SERIAL PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            embedding vector(768),
            search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            CONSTRAINT uq_knowledge_chunk_position UNIQUE (document_id, chunk_index)
        )
        """
    )
    op.execute("CREATE INDEX ix_knowledge_chunks_document ON knowledge_chunks(document_id)")
    op.execute("CREATE INDEX ix_knowledge_chunks_search ON knowledge_chunks USING GIN(search_vector)")
    op.execute("CREATE INDEX ix_knowledge_chunks_embedding ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute(
        """
        CREATE TABLE knowledge_jobs (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            job_type VARCHAR(40) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'queued',
            source_uri VARCHAR(1000),
            error_message TEXT,
            documents_indexed INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_knowledge_jobs_owner_id ON knowledge_jobs(owner_id)")
    op.execute("CREATE INDEX ix_knowledge_jobs_status ON knowledge_jobs(status)")
    op.execute(
        """
        CREATE TABLE model_calls (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
            provider VARCHAR(60) NOT NULL,
            model_name VARCHAR(200) NOT NULL,
            status VARCHAR(30) NOT NULL,
            latency_ms INTEGER NOT NULL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_model_calls_owner_id ON model_calls(owner_id)")
    op.execute("CREATE INDEX ix_model_calls_incident_id ON model_calls(incident_id)")
    op.execute("CREATE INDEX ix_model_calls_status ON model_calls(status)")
    op.execute(
        """
        CREATE TABLE investigation_reports (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            model_call_id INTEGER REFERENCES model_calls(id) ON DELETE SET NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'completed',
            report_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_investigation_reports_owner_id ON investigation_reports(owner_id)")
    op.execute("CREATE INDEX ix_investigation_reports_incident_id ON investigation_reports(incident_id)")
    op.execute("CREATE INDEX ix_investigation_reports_status ON investigation_reports(status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS investigation_reports")
    op.execute("DROP TABLE IF EXISTS model_calls")
    op.execute("DROP TABLE IF EXISTS knowledge_jobs")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.execute("DROP TABLE IF EXISTS knowledge_documents")
    op.execute("DROP TABLE IF EXISTS repository_connections")
