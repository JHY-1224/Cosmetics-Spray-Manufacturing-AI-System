"""Persist signed distribution lifecycle and acknowledgements."""

from alembic import op
from sqlalchemy import inspect, text
from quality.models import Distribution

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "distributions" not in inspect(bind).get_table_names():
        Distribution.__table__.create(bind)
        if bind.dialect.name == "postgresql":
            bind.execute(text("ALTER TABLE distributions ENABLE ROW LEVEL SECURITY"))
            bind.execute(text("ALTER TABLE distributions FORCE ROW LEVEL SECURITY"))
            bind.execute(
                text("""CREATE POLICY scope_isolation ON distributions USING (
              tenant_id=current_setting('app.tenant',true) AND
              (site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR '*'=ANY(string_to_array(current_setting('app.sites',true),','))) AND
              (scope_line_id='' OR scope_line_id=ANY(string_to_array(current_setting('app.lines',true),',')) OR '*'=ANY(string_to_array(current_setting('app.lines',true),','))))""")
            )


def downgrade():
    raise RuntimeError("配置分发审计必须保留，回退采用独立恢复")
