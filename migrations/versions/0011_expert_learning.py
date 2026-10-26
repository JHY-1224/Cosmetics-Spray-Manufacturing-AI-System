"""Append expert records, durable bounded jobs and exclusive evaluation groups."""

from alembic import op
import sqlalchemy as sa
from quality.models import LearningRecord, LearningTask, StudyGroup

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for cls in (LearningRecord, LearningTask, StudyGroup):
        cls.__table__.create(bind, checkfirst=True)
        if bind.dialect.name == "postgresql":
            name = cls.__tablename__
            bind.execute(sa.text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
            bind.execute(sa.text(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY'))
            bind.execute(sa.text(f'DROP POLICY IF EXISTS scope_isolation ON "{name}"'))
            bind.execute(
                sa.text(f'''CREATE POLICY scope_isolation ON "{name}" USING (
              tenant_id=current_setting('app.tenant',true) AND
              (site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR '*'=ANY(string_to_array(current_setting('app.sites',true),','))) AND
              (scope_line_id='' OR scope_line_id=ANY(string_to_array(current_setting('app.lines',true),',')) OR '*'=ANY(string_to_array(current_setting('app.lines',true),','))))''')
            )
    root = (
        "snapshot ->> 'root_id'"
        if bind.dialect.name == "postgresql"
        else "json_extract(snapshot, '$.root_id')"
    )
    bind.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS ix_learning_active_root ON learning_records (tenant_id, site_id, kind, status, ({root}))"
        )
    )


def downgrade():
    raise RuntimeError("专家原稿、审核、资料及评估分组必须保留；使用独立备份恢复，不删表回滚")
