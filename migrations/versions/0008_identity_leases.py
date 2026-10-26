"""Persist center authorization versions and bounded offline identity leases."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    names = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    for column in [
        sa.Column("auth_origin", sa.String(30), nullable=False, server_default="local"),
        sa.Column("center_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("central_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        if column.name not in names:
            op.add_column("users", column)


def downgrade():
    raise RuntimeError("授权租约与撤销版本必须保留，不允许直接移除安全字段")
