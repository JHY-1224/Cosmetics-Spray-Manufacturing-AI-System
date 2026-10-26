"""Industrial quality, formulation, preauthorization and business coordination domains.

Only additive new tables. Historical migrations remain unchanged; test fixtures retain actual 0011 DDL.
"""

from alembic import op
import sqlalchemy as sa
from pathlib import Path
import hashlib
import json

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

SCHEMA_SHA256 = "594c4044712066d0df786961ff41c6416f16d938b4815c7b4befef4a01f30cf7"
APPEND_ONLY = {
    "quality_assessments",
    "actual_material_usage",
    "business_receipts",
    "business_documents",
    "business_document_lines",
    "inventory_snapshots",
    "tool_invocations",
}


def upgrade():
    bind = op.get_bind()
    raw = Path(__file__).with_name("0012-schema.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA256:
        raise RuntimeError("0012冻结DDL校验失败，请核对离线包完整性；不继续迁移")
    definitions = json.loads(raw)[bind.dialect.name]
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in definitions:
        name = table["name"]
        if name not in existing:
            bind.execute(sa.text(table["create"]))
        indexes = {i['name'] for i in sa.inspect(bind).get_indexes(name)}
        for index in table['indexes']:
            if index['name'] not in indexes:
                bind.execute(sa.text(index['create']))
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY'))
            bind.execute(sa.text(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY'))
            bind.execute(sa.text(f'DROP POLICY IF EXISTS scope_isolation ON "{name}"'))
            policy = """tenant_id=current_setting('app.tenant',true) AND
              (site_id=ANY(string_to_array(current_setting('app.sites',true),',')) OR
               '*'=ANY(string_to_array(current_setting('app.sites',true),','))) AND
              (scope_line_id='' OR scope_line_id=ANY(string_to_array(current_setting('app.lines',true),',')) OR
               '*'=ANY(string_to_array(current_setting('app.lines',true),',')))"""
            bind.execute(
                sa.text(f'CREATE POLICY scope_isolation ON "{name}" USING ({policy}) WITH CHECK ({policy})')
            )
        if name in APPEND_ONLY:
            if bind.dialect.name == "postgresql":
                bind.execute(
                    sa.text("""CREATE OR REPLACE FUNCTION industrial_append_only() RETURNS trigger
                  LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'industrial source fact is append-only'; END; $$""")
                )
                bind.execute(sa.text(f'DROP TRIGGER IF EXISTS industrial_immutable ON "{name}"'))
                bind.execute(
                    sa.text(
                        f'CREATE TRIGGER industrial_immutable BEFORE UPDATE OR DELETE ON "{name}" FOR EACH ROW EXECUTE FUNCTION industrial_append_only()'
                    )
                )
            else:
                for operation in ("UPDATE", "DELETE"):
                    trigger = name + "_no_" + operation.lower()
                    bind.execute(
                        sa.text(
                            f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {name} BEGIN SELECT RAISE(ABORT, 'industrial source fact is append-only'); END"
                        )
                    )


def downgrade():
    raise RuntimeError("新质量、材料与业务事实禁止删表降级；使用独立备份恢复并核对外部命令，保留全部原始记录")
