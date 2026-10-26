"""Keep durable local evidence and explicit S3 replication status."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "replication_status" not in {c["name"] for c in sa.inspect(bind).get_columns("evidence")}:
        op.add_column(
            "evidence", sa.Column("replication_status", sa.String(24), nullable=False, server_default="local")
        )
        op.create_index("ix_evidence_replication_status", "evidence", ["replication_status"])


def downgrade():
    raise RuntimeError("证据复制状态不可直接移除；按备份恢复流程回退")
