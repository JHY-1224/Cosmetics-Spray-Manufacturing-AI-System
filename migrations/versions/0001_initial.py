"""Initial business schema, RLS and immutable evidence ledger.

Revision ID: 0001
"""

from alembic import op
from sqlalchemy import text
from quality.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind)
    if bind.dialect.name == "postgresql":
        for name in Base.metadata.tables:
            # Names come exclusively from SQLAlchemy metadata, never user input.
            bind.execute(text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
            bind.execute(text(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY'))
            bind.execute(
                text(f'''CREATE POLICY scope_isolation ON "{name}" USING (
                tenant_id = current_setting('app.tenant', true) AND
                (site_id = '' OR site_id = ANY(string_to_array(current_setting('app.sites', true), ',')) OR
                 '*' = ANY(string_to_array(current_setting('app.sites', true), ',')))) WITH CHECK (
                tenant_id = current_setting('app.tenant', true) AND
                (site_id = '' OR site_id = ANY(string_to_array(current_setting('app.sites', true), ',')) OR
                 '*' = ANY(string_to_array(current_setting('app.sites', true), ','))))''')
            )
        bind.execute(
            text(
                "CREATE INDEX ix_knowledge_search ON knowledge USING gin(to_tsvector('simple', title || ' ' || content))"
            )
        )
        bind.execute(
            text(
                "CREATE INDEX ix_measurement_history ON measurements (tenant_id,site_id,equipment_id,metric,occurred_at)"
            )
        )
        bind.execute(
            text(
                "CREATE INDEX ix_inspection_object_time ON inspections (tenant_id,site_id,object_id,occurred_at)"
            )
        )


def downgrade():
    raise RuntimeError("初始迁移包含业务数据；禁止自动删表降版，请恢复经过校验的独立备份")
