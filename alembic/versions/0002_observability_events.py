"""add ingestion credentials and event storage"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_observability_events"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("services", sa.Column("ingest_api_key_hash", sa.String(length=64), nullable=True))
    op.add_column("services", sa.Column("ingest_api_key_prefix", sa.String(length=16), nullable=True))
    op.create_index("ix_services_ingest_api_key_hash", "services", ["ingest_api_key_hash"], unique=True)
    # Existing Phase 1 services need a key before they can ingest. The application
    # will issue one through the rotation endpoint; keep these columns nullable for
    # backward-compatible migration of already-created services.
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("fingerprint", name="uq_events_fingerprint"),
    )
    op.create_index("ix_events_service_id", "events", ["service_id"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_severity", "events", ["severity"])
    op.create_index("ix_events_fingerprint", "events", ["fingerprint"])
    op.create_index("ix_events_service_occurred_at", "events", ["service_id", "occurred_at"])
    op.create_index("ix_events_severity_occurred_at", "events", ["severity", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_events_severity_occurred_at", table_name="events")
    op.drop_index("ix_events_service_occurred_at", table_name="events")
    op.drop_index("ix_events_fingerprint", table_name="events")
    op.drop_index("ix_events_severity", table_name="events")
    op.drop_index("ix_events_occurred_at", table_name="events")
    op.drop_index("ix_events_service_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_services_ingest_api_key_hash", table_name="services")
    op.drop_column("services", "ingest_api_key_prefix")
    op.drop_column("services", "ingest_api_key_hash")
