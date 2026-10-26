"""Keep broker delivery and center replication acknowledgements independent."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("outbox")}
    if "synced" not in columns:
        op.add_column("outbox", sa.Column("synced", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.create_index("ix_outbox_synced", "outbox", ["synced"])


def downgrade():
    raise RuntimeError("同步确认不能无损降版；使用备份恢复或兼容前向迁移")
