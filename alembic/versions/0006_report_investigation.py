"""link investigation reports to agent investigations"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_report_investigation"
down_revision: Union[str, None] = "0005_investigations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("investigation_reports", sa.Column("investigation_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_investigation_reports_investigation", "investigation_reports", "investigations", ["investigation_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_investigation_reports_investigation_id", "investigation_reports", ["investigation_id"])


def downgrade() -> None:
    op.drop_index("ix_investigation_reports_investigation_id", table_name="investigation_reports")
    op.drop_constraint("fk_investigation_reports_investigation", "investigation_reports", type_="foreignkey")
    op.drop_column("investigation_reports", "investigation_id")
