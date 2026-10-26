"""Durable reception ledger, governed source registry, append-only application audit."""

from alembic import op
import sqlalchemy as sa
from quality.models import RawRecord

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Command replay stores the recoverable business result, never one-time credentials.
    commands = sa.table('commands', sa.column('id'), sa.column('response', sa.JSON()))
    def redact(value):
        if isinstance(value, dict):
            return {k: None if k.lower() in {'token','sync_token','password','api_key','secret_key'} else redact(v)
                    for k, v in value.items()}
        return [redact(v) for v in value] if isinstance(value, list) else value
    for row in bind.execute(sa.select(commands.c.id, commands.c.response)).mappings():
        clean = redact(row['response'])
        if clean != row['response']:
            bind.execute(commands.update().where(commands.c.id == row['id']).values(response=clean))
    if 'source_registry' not in {c['name'] for c in sa.inspect(bind).get_columns('adapters')}:
        op.add_column('adapters', sa.Column('source_registry', sa.JSON(), nullable=False, server_default='{}'))
    if 'review_flags' not in {c['name'] for c in sa.inspect(bind).get_columns('cases')}:
        op.add_column('cases', sa.Column('review_flags', sa.JSON(), nullable=False, server_default='[]'))
    if 'authorization_hash' not in {c['name'] for c in sa.inspect(bind).get_columns('commands')}:
        op.add_column('commands', sa.Column('authorization_hash', sa.String(64), nullable=False, server_default=''))
    if RawRecord.__tablename__ not in sa.inspect(bind).get_table_names():
        RawRecord.__table__.create(bind)
    if bind.dialect.name == 'postgresql':
        bind.execute(sa.text('ALTER TABLE raw_records ENABLE ROW LEVEL SECURITY'))
        bind.execute(sa.text('ALTER TABLE raw_records FORCE ROW LEVEL SECURITY'))
        bind.execute(sa.text('DROP POLICY IF EXISTS scope_isolation ON raw_records'))
        bind.execute(sa.text("""CREATE POLICY scope_isolation ON raw_records USING (
          tenant_id=current_setting('app.tenant',true) AND
          (site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR '*'=ANY(string_to_array(current_setting('app.sites',true),','))) AND
          (scope_line_id='' OR scope_line_id=ANY(string_to_array(current_setting('app.lines',true),',')) OR '*'=ANY(string_to_array(current_setting('app.lines',true),','))))"""))
        bind.execute(sa.text("""CREATE OR REPLACE FUNCTION quality_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'audit history is append-only'; END; $$"""))
        bind.execute(sa.text('DROP TRIGGER IF EXISTS quality_audit_immutable ON audit'))
        bind.execute(sa.text('CREATE TRIGGER quality_audit_immutable BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION quality_audit_append_only()'))
    else:
        bind.execute(sa.text("CREATE TRIGGER IF NOT EXISTS quality_audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit history is append-only'); END"))
        bind.execute(sa.text("CREATE TRIGGER IF NOT EXISTS quality_audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit history is append-only'); END"))


def downgrade():
    raise RuntimeError('原始接收记录与审计历史必须保留；请使用经过验证的独立恢复')
