"""Line authorization also enforced in PostgreSQL policies."""

from alembic import op
import sqlalchemy as sa
from quality.models import Base

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for name in Base.metadata.tables:
        if name not in sa.inspect(bind).get_table_names():
            continue
        columns = {c["name"] for c in sa.inspect(bind).get_columns(name)}
        if "scope_line_id" not in columns:
            op.add_column(name, sa.Column("scope_line_id", sa.String(100), nullable=False, server_default=""))
            op.create_index("ix_" + name + "_scope_line_id", name, ["scope_line_id"])
        if name == "users" and "allowed_lines" not in columns:
            op.add_column(name, sa.Column("allowed_lines", sa.JSON(), nullable=False, server_default="[]"))
        if name == "masters":
            bind.execute(
                sa.text("UPDATE masters SET scope_line_id=id WHERE kind='line' AND scope_line_id=''")
            )
        elif name in ("cases", "inspections"):
            bind.execute(sa.text(f"UPDATE {name} SET scope_line_id=line_id WHERE scope_line_id=''"))
        elif name == "evidence":
            bind.execute(
                sa.text("UPDATE evidence SET scope_line_id='legacy-unassigned' WHERE scope_line_id=''")
            )
        elif name in ("plans", "approvals", "receipts", "retests", "runs"):
            bind.execute(
                sa.text(
                    f"UPDATE {name} SET scope_line_id=(SELECT line_id FROM cases WHERE cases.id={name}.case_id) WHERE scope_line_id=''"
                )
            )
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text(f'DROP POLICY IF EXISTS scope_isolation ON "{name}"'))
            bind.execute(
                sa.text(f'''CREATE POLICY scope_isolation ON "{name}" USING (
              tenant_id=current_setting('app.tenant',true) AND
              (site_id='' OR site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR '*'=ANY(string_to_array(current_setting('app.sites',true),','))) AND
              (scope_line_id='' OR scope_line_id=ANY(string_to_array(current_setting('app.lines',true),',')) OR '*'=ANY(string_to_array(current_setting('app.lines',true),','))))''')
            )


def downgrade():
    raise RuntimeError("禁止移除隔离策略，请使用经过验证的备份恢复")
