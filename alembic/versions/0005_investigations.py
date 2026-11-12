"""add multi-agent investigation storage"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_investigations"
down_revision: Union[str, None] = "0004_repository_service"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("priority", sa.String(length=30), nullable=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("iteration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_iterations", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index("ix_investigations_owner_id", "investigations", ["owner_id"])
    op.create_index("ix_investigations_incident_id", "investigations", ["incident_id"])
    op.create_index("ix_investigations_thread_id", "investigations", ["thread_id"])
    op.create_index("ix_investigations_status", "investigations", ["status"])
    op.create_index("ix_investigations_owner_status", "investigations", ["owner_id", "status"])
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("investigation_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_executions_investigation_id", "agent_executions", ["investigation_id"])
    op.create_index("ix_agent_executions_agent_name", "agent_executions", ["agent_name"])
    op.create_index("ix_agent_executions_status", "agent_executions", ["status"])
    op.create_index("ix_agent_executions_investigation_agent", "agent_executions", ["investigation_id", "agent_name"])
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("investigation_id", sa.Integer(), nullable=False),
        sa.Column("agent_execution_id", sa.Integer(), nullable=True),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_execution_id"], ["agent_executions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tool_executions_investigation_id", "tool_executions", ["investigation_id"])
    op.create_index("ix_tool_executions_agent_execution_id", "tool_executions", ["agent_execution_id"])
    op.create_index("ix_tool_executions_tool_name", "tool_executions", ["tool_name"])
    op.create_index("ix_tool_executions_status", "tool_executions", ["status"])
    op.create_table(
        "investigation_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("investigation_id", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=60), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_investigation_checkpoints_investigation_id", "investigation_checkpoints", ["investigation_id"])
    op.create_table(
        "investigation_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("investigation_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("action_type", sa.String(length=60), nullable=False),
        sa.Column("proposal_json", sa.JSON(), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_investigation_approvals_investigation_id", "investigation_approvals", ["investigation_id"])
    op.create_index("ix_investigation_approvals_owner_id", "investigation_approvals", ["owner_id"])
    op.create_index("ix_investigation_approvals_status", "investigation_approvals", ["status"])


def downgrade() -> None:
    for index, table in [
        ("ix_investigation_approvals_status", "investigation_approvals"),
        ("ix_investigation_approvals_owner_id", "investigation_approvals"),
        ("ix_investigation_approvals_investigation_id", "investigation_approvals"),
    ]:
        op.drop_index(index, table_name=table)
    op.drop_table("investigation_approvals")
    op.drop_index("ix_investigation_checkpoints_investigation_id", table_name="investigation_checkpoints")
    op.drop_table("investigation_checkpoints")
    for index, table in [
        ("ix_tool_executions_status", "tool_executions"),
        ("ix_tool_executions_tool_name", "tool_executions"),
        ("ix_tool_executions_agent_execution_id", "tool_executions"),
        ("ix_tool_executions_investigation_id", "tool_executions"),
    ]:
        op.drop_index(index, table_name=table)
    op.drop_table("tool_executions")
    for index, table in [
        ("ix_agent_executions_investigation_agent", "agent_executions"),
        ("ix_agent_executions_status", "agent_executions"),
        ("ix_agent_executions_agent_name", "agent_executions"),
        ("ix_agent_executions_investigation_id", "agent_executions"),
    ]:
        op.drop_index(index, table_name=table)
    op.drop_table("agent_executions")
    for index in ["ix_investigations_owner_status", "ix_investigations_status", "ix_investigations_thread_id", "ix_investigations_incident_id", "ix_investigations_owner_id"]:
        op.drop_index(index, table_name="investigations")
    op.drop_table("investigations")
