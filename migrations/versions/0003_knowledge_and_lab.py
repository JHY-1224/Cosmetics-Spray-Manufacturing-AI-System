"""Authorized pgvector index and instrument/manual test evidence."""

from alembic import op
from sqlalchemy import inspect, text
from quality.models import KnowledgeVector, LabResult

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    existing = set(inspect(bind).get_table_names())
    for cls in (KnowledgeVector, LabResult):
        if cls.__tablename__ not in existing:
            cls.__table__.create(bind)
            if bind.dialect.name == "postgresql":
                name = cls.__tablename__
                bind.execute(text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
                bind.execute(text(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY'))
                bind.execute(
                    text(f'''CREATE POLICY scope_isolation ON "{name}" USING (
                    tenant_id=current_setting('app.tenant',true) AND (site_id='' OR site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR '*'=ANY(string_to_array(current_setting('app.sites',true),','))))''')
                )


def downgrade():
    raise RuntimeError("保留知识与检验记录；回退使用独立备份")
