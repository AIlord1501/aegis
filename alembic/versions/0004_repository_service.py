"""link repository connections to services"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_repository_service"
down_revision: Union[str, None] = "0003_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("repository_connections", sa.Column("service_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_repository_connections_service", "repository_connections", "services", ["service_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_repository_connections_service_id", "repository_connections", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_repository_connections_service_id", table_name="repository_connections")
    op.drop_constraint("fk_repository_connections_service", "repository_connections", type_="foreignkey")
    op.drop_column("repository_connections", "service_id")
