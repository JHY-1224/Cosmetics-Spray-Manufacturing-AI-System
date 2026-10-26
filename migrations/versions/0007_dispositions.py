"""Independent human approval of rework/strip disposition and bounded re-entry."""

from alembic import op
from sqlalchemy import inspect, text
from quality.models import Disposition

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "dispositions" not in inspect(bind).get_table_names():
        Disposition.__table__.create(bind)
        if bind.dialect.name == "postgresql":
            bind.execute(text("ALTER TABLE dispositions ENABLE ROW LEVEL SECURITY"))
            bind.execute(text("ALTER TABLE dispositions FORCE ROW LEVEL SECURITY"))
            bind.execute(
                text("""CREATE POLICY scope_isolation ON dispositions USING (
              tenant_id=current_setting('app.tenant',true) AND
              (site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR '*'=ANY(string_to_array(current_setting('app.sites',true),','))) AND
              (scope_line_id='' OR scope_line_id=ANY(string_to_array(current_setting('app.lines',true),',')) OR '*'=ANY(string_to_array(current_setting('app.lines',true),','))))""")
            )


def downgrade():
    raise RuntimeError("质量处置与经过记录必须保留，回退采用独立恢复")
